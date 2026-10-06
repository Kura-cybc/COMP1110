import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import app
from knowledge_base import KNOWLEDGE_PATH, markdown_links, request_knowledge, retrieve_knowledge
from models import Deadline, DocumentRequirement, Draft, Evidence, Fact, Programme, ResearchRequest, Source, Step
from research import LoginRequiredError, Page, deepseek_browser_search, normalize, read_page, research, verify_draft


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest(destination="United Kingdom", start_date=date(2027, 6, 1),
            end_date=date(2027, 8, 31), programme_type="summer_school",
            programme_url="https://example.edu/summer")

    def test_country_and_document_queries(self):
        matches = request_knowledge(self.request)
        self.assertTrue(any("6.6 英国" in match.title for match in matches))
        self.assertIn("不是所有 HKU 学生通用", next(match.text for match in matches if "6.6 英国" in match.title))
        self.assertFalse(any("6.3 日本" in match.title for match in matches))
        self.assertIn("证明文件", retrieve_knowledge("成绩单")[0].title)
        self.assertLessEqual(len(matches), 8)
        self.assertFalse(any("已读文章" in match.title for match in matches))

    def test_balanced_urls_and_repeated_labels_are_preserved(self):
        links = markdown_links("[表](https://a.edu/file(2026).pdf) · [表](https://b.edu/file.pdf)")
        self.assertEqual(set(links.values()), {"https://a.edu/file(2026).pdf", "https://b.edu/file.pdf"})

    def test_markdown_is_reloaded_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kb.md"
            path.write_text("## 证明\n成绩单费用 40\n", encoding="utf-8")
            self.assertIn("40", retrieve_knowledge("成绩单", path=path)[0].text)
            path.write_text("## 证明\n成绩单费用 50\n", encoding="utf-8")
            self.assertIn("50", retrieve_knowledge("成绩单", path=path)[0].text)

    def test_query_and_document_endpoints_need_no_key(self):
        client = TestClient(app)
        response = client.get("/api/knowledge-base", params={"query": "英国签证"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("英国" in match["title"] for match in response.json()["matches"]))
        document = client.get("/api/knowledge-base/document")
        self.assertEqual(document.status_code, 200)
        self.assertIn("HKU 海外学习知识库", document.text)
        self.assertEqual(document.text, KNOWLEDGE_PATH.read_text(encoding="utf-8"))

    def test_local_claim_cannot_self_certify_as_verified(self):
        page = Page(source=Source(id="KB1", url="/api/knowledge-base/document", title="Local",
            publisher_type="knowledge_base", retrieved_at=datetime.now(timezone.utc), read_status="read"),
            text=normalize("Apply online. Applications close 1 March 2027. Upload a transcript."), links={})
        draft = Draft(programmes=[Programme(id="p1", name="Example", programme_type="summer_school",
            destination="UK", steps=[Step(id="apply", category="host_application", title="Apply",
                action=Fact(value="Apply online", state="verified", evidence=[Evidence(source_id="KB1", quote="Apply online")]),
                deadline=Deadline(kind="fixed", date="2027-03-01", state="verified",
                    evidence=[Evidence(source_id="KB1", quote="Applications close 1 March 2027")]),
                documents=[DocumentRequirement(name="Transcript", state="verified",
                    evidence=[Evidence(source_id="KB1", quote="Upload a transcript")])])])])
        programmes, _ = verify_draft(draft, self.request, [page])
        step = programmes[0].steps[0]
        self.assertEqual(step.action.state, "needs_review")
        self.assertEqual(step.deadline.state, "needs_review")
        self.assertEqual(step.documents[0].state, "needs_review")

    def test_login_redirect_is_not_followed(self):
        calls = []
        def respond(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://portal.edu/login"})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client, patch(
            "research.safe_public_url", side_effect=lambda url: url):
            with self.assertRaises(LoginRequiredError):
                read_page(client, "https://example.edu/application", "S1")
        self.assertEqual(calls, ["https://example.edu/application"])

    def test_password_form_and_401_are_not_application_content(self):
        for status, html in [(200, '<html><input type="password"></html>'), (401, "Unauthorized")]:
            with self.subTest(status=status), httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text=html, headers={"content-type": "text/html"}))) as client, patch(
                "research.safe_public_url", side_effect=lambda url: url):
                with self.assertRaises(LoginRequiredError):
                    read_page(client, "https://example.edu/secure", "S1")

    def test_public_article_with_login_navigation_is_still_readable(self):
        html = '<html><body><a href="/login">Log in</a><main>' + "Application requirements. " * 20 + '</main></body></html>'
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html,
            headers={"content-type": "text/html"}))) as client, patch("research.safe_public_url", side_effect=lambda url: url):
            page = read_page(client, "https://example.edu/article", "S1")
        self.assertEqual(page.source.read_status, "read")

    def test_pipeline_reads_kb_urls_and_exports_login_links(self):
        snippet = retrieve_knowledge("英国签证")[0]
        snippet.links = {"Apply": "https://example.edu/login"}
        urls, captured = [], []
        def read(_client, url, source_id):
            urls.append(url)
            if url.endswith("/login"):
                raise LoginRequiredError("需要登录")
            return Page(source=Source(id=source_id, url=url, title="Official",
                publisher_type="host", retrieved_at=datetime.now(timezone.utc), read_status="read"),
                text=normalize("Public programme information. " * 10), links={})
        def extract(request, pages, provider, model, key):
            captured.extend(pages)
            return Draft()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake"}), patch(
            "research.request_knowledge", return_value=[snippet]), patch("research.read_page", side_effect=read), patch(
            "research.extract_with_ai", side_effect=extract):
            result = research(self.request, provider="deepseek", search_backend="manual")
        self.assertIn("https://example.edu/login", urls)
        self.assertTrue(any(page.source.publisher_type == "knowledge_base" for page in captured))
        login = next(source for source in result.sources if source.url == "https://example.edu/login")
        self.assertEqual(login.read_status, "login_required")
        self.assertEqual(result.knowledge_matches[0].id, snippet.id)
        json.loads(result.model_dump_json())

    def test_new_name_and_legacy_settings(self):
        client = TestClient(app)
        self.assertIn("ExchangePrep", client.get("/").text)
        with patch.dict("os.environ", {"EXCHANGEPREP_LLM_PROVIDER": "deepseek", "PATHLOOM_LLM_PROVIDER": "openai"}):
            self.assertEqual(client.get("/api/config").json()["llm_provider"], "deepseek")

    def test_agent_keeps_login_link_even_if_not_selected_for_crawling(self):
        call = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_web",
            arguments='{"url":"https://example.edu/login"}'))
        replies = iter([
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"selected_urls":[]}', tool_calls=[]))]),
        ])
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: next(replies))))
        log = []
        with patch("research.OpenAI", return_value=fake_client), patch(
            "research.browser_search_query", return_value=[]), patch(
            "research.read_page", side_effect=LoginRequiredError("需要登录")):
            deepseek_browser_search(self.request, "fake", "test", seed_urls=["https://example.edu/summer"],
                knowledge_context=[{"links": {"Apply": "https://example.edu/login"}}], login_sources=log)
        self.assertEqual(log[0].url, "https://example.edu/login")
        self.assertEqual(log[0].read_status, "login_required")


if __name__ == "__main__":
    unittest.main()
