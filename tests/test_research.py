import unittest
import json
import httpx
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from browser_search import result_url
from models import Deadline, DocumentRequirement, Draft, Evidence, Fact, Programme, ResearchRequest, Source, Step
from research import (
    Page, deepseek_browser_search, extract_with_ai, hku_catalog_search,
    follow_up_queries, linked_detail_urls, normalize, openai_search_query,
    page_excerpt, prioritize_urls, read_page, run_browser_search, verify_draft,
)


class ResearchContractTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest(
            destination="United Kingdom", start_date=date(2027, 6, 1),
            end_date=date(2027, 8, 31), programme_type="summer_school",
            major="Computer Science", programme_url="https://www.example.edu/summer",
        )
        self.page = Page(
            source=Source(
                id="S1", url="https://www.example.edu/summer", title="Summer",
                publisher_type="host", retrieved_at=datetime.now(timezone.utc),
                read_status="read",
            ),
            text=normalize("Applications close on 1 March 2027. Apply online. Upload a transcript."),
            links={"Apply online": "https://www.example.edu/apply"},
        )

    def test_prioritizes_direct_and_hku_pages(self):
        urls = prioritize_urls(self.request, [f"https://school{i}.edu/page" for i in range(20)])
        self.assertEqual(urls[0], "https://www.example.edu/summer")
        self.assertTrue(any("intlaffairs.hku.hk" in url for url in urls))
        self.assertLessEqual(len(urls), 12)

    def test_evidence_and_missing_steps_are_handed_off(self):
        step = Step(
            id="host_apply", category="host_application", title="目标院校申请",
            action=Fact(value="Apply online", evidence=[Evidence(source_id="S1", quote="Apply online")]),
            channel=Fact(value="https://www.example.edu/apply", evidence=[Evidence(source_id="S1", quote="Apply online")]),
            deadline=Deadline(kind="fixed", date="2027-03-01", raw="Applications close on 1 March 2027",
                              evidence=[Evidence(source_id="S1", quote="Applications close on 1 March 2027")]),
        )
        draft = Draft(programmes=[Programme(
            id="p1", name="Summer", official_url="https://www.example.edu/summer",
            programme_type="summer_school", destination="United Kingdom", steps=[step],
        )])
        programmes, warnings = verify_draft(draft, self.request, [self.page])
        self.assertEqual(programmes[0].steps[0].deadline.state, "verified")
        self.assertEqual(programmes[0].steps[0].channel.state, "verified")
        self.assertIn("visa", {item.category for item in programmes[0].steps})
        self.assertTrue(programmes[0].unresolved_questions)
        self.assertFalse(any("目标院校申请 截止日期" in warning for warning in warnings))

    def test_fabricated_quote_is_downgraded(self):
        draft = Draft(programmes=[Programme(
            id="p1", name="Summer", programme_type="summer_school", destination="United Kingdom",
            steps=[Step(
                id="host_apply", category="host_application", title="目标院校申请",
                deadline=Deadline(kind="fixed", date="2027-03-01",
                                  evidence=[Evidence(source_id="S1", quote="Deadline is 1 March 2027")]),
            )],
        )])
        programmes, warnings = verify_draft(draft, self.request, [self.page])
        self.assertEqual(programmes[0].steps[0].deadline.state, "needs_review")
        self.assertTrue(warnings)

    def test_previous_calendar_year_can_be_valid_for_next_summer(self):
        page = Page(
            source=self.page.source,
            text=normalize("Applications for summer 2027 close on 1 December 2026."), links={},
        )
        draft = Draft(programmes=[Programme(
            id="p1", name="Summer", programme_type="summer_school", destination="United Kingdom",
            steps=[Step(
                id="host_apply", category="host_application", title="目标院校申请",
                deadline=Deadline(kind="fixed", date="2026-12-01",
                                  evidence=[Evidence(source_id="S1", quote="Applications for summer 2027 close on 1 December 2026")]),
            )],
        )])
        programmes, _ = verify_draft(draft, self.request, [page])
        self.assertEqual(programmes[0].steps[0].deadline.state, "verified")

    def test_api_rejects_missing_keys_with_clear_error(self):
        with patch.dict("os.environ", {"PATHLOOM_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": ""}):
            response = TestClient(app).post("/api/research", json=self.request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("DEEPSEEK_API_KEY", response.json()["detail"])

    def test_mocked_deepseek_brave_pipeline_returns_handoff_json(self):
        def fake_read_page(_client, url, source_id):
            kind = "hku" if "hku.hk" in url else "host"
            return Page(
                source=Source(
                    id=source_id, url=url, title="Official page", publisher_type=kind,
                    retrieved_at=datetime.now(timezone.utc), read_status="read",
                ),
                text=normalize("Applications close on 1 March 2027. Apply online. Upload a transcript."),
                links={"Apply online": "https://www.example.edu/apply"},
            )

        draft = Draft(programmes=[Programme(
            id="p1", name="Summer", official_url="https://www.example.edu/summer",
            programme_type="summer_school", destination="United Kingdom",
            steps=[Step(
                id="host_apply", category="host_application", title="目标院校申请",
                action=Fact(value="Apply online", evidence=[Evidence(source_id="S1", quote="Apply online")]),
                channel=Fact(value="https://www.example.edu/apply", evidence=[Evidence(source_id="S1", quote="Apply online")]),
                deadline=Deadline(kind="fixed", date="2027-03-01",
                                  evidence=[Evidence(source_id="S1", quote="Applications close on 1 March 2027")]),
            )],
        )])
        with patch.dict("os.environ", {
            "PATHLOOM_LLM_PROVIDER": "deepseek", "PATHLOOM_SEARCH_PROVIDER": "brave",
            "DEEPSEEK_API_KEY": "test-key", "BRAVE_API_KEY": "test-search-key",
        }), patch("research.brave_search", return_value=[]), \
             patch("research.read_page", side_effect=fake_read_page), \
             patch("research.extract_with_ai", return_value=draft), \
             patch("research.brave_search_query", return_value=[]):
            response = TestClient(app).post("/api/research", json=self.request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["provider"].startswith("deepseek:"))
        self.assertEqual(result["programmes"][0]["steps"][0]["deadline"]["state"], "verified")
        self.assertEqual(result["programmes"][0]["steps"][0]["channel"]["state"], "verified")
        self.assertTrue(result["sources"])

    def test_home_page_served(self):
        response = TestClient(app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("开始联网调查", response.text)

    def test_deepseek_adapter_parses_json_mode(self):
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"programmes": []}')
            )])
        )))
        with patch("research.OpenAI", return_value=fake_client) as client_class:
            result = extract_with_ai(self.request, [self.page], "deepseek", "deepseek-flash", "test-key")
        self.assertEqual(result.programmes, [])
        self.assertEqual(client_class.call_args.kwargs["base_url"], "https://api.deepseek.com")

    def test_openai_web_search_reads_source_metadata(self):
        fake_response = SimpleNamespace(model_dump=lambda: {"output": [
            {"type": "web_search_call", "action": {"sources": [{"url": "https://www.example.edu/summer"}]}},
            {"type": "message", "content": [{"annotations": [{"url": "https://www.example.edu/summer"}]}]},
        ]})
        fake_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: fake_response))
        with patch("research.OpenAI", return_value=fake_client):
            urls = openai_search_query("find official pages", "test-key", "gpt-5-mini")
        self.assertEqual(urls, ["https://www.example.edu/summer"])

    def test_deepseek_agent_calls_browser_tool(self):
        call = SimpleNamespace(id="call-1", function=SimpleNamespace(
            name="search_web", arguments='{"query":"UK 2027 summer school official"}',
        ))
        replies = iter([
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[],
                content='{"selected_urls":["https://www.example.edu/summer","https://fabricated.edu/summer"]}'))]),
        ])
        calls = []
        def fake_create(**kwargs):
            calls.append(kwargs)
            return next(replies)
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=fake_create
        )))
        def fake_browser(query):
            if "xiaohongshu" in query:
                return [{"title": "HKU student summer experience", "url": "https://www.xiaohongshu.com/explore/abc",
                         "snippet": "I applied to Example University last summer", "query": query}]
            return [{"title": "Summer school", "url": "https://www.example.edu/summer"}]
        clues, search_log = [], []
        with patch("research.OpenAI", return_value=fake_client), patch(
            "research.browser_search_query", side_effect=fake_browser,
        ) as browser:
            urls = deepseek_browser_search(self.request, "test-key", "deepseek-flash",
                                            clues=clues, search_log=search_log)
        browser.assert_any_call("UK 2027 summer school official")
        self.assertEqual(browser.call_count, 3)
        self.assertEqual(urls[0], "https://www.example.edu/summer")
        self.assertNotIn("https://fabricated.edu/summer", urls)
        self.assertEqual(len(clues), 1)
        self.assertEqual(search_log[0].purpose, "social_discovery")
        self.assertEqual(calls[0]["tool_choice"], "required")
        self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "disabled"}})

    def test_hku_directory_discovers_destination_and_season(self):
        html = """<table>
          <tr><td>UK</td><td>Example University</td><td><a href="https://www.example.edu/summer">Summer School</a></td><td>Summer</td></tr>
          <tr><td>UK</td><td>Example University</td><td><a href="https://www.example.edu/winter">Winter School</a></td><td>Winter</td></tr>
          <tr><td>Japan</td><td>Other</td><td><a href="https://other.edu/summer">Summer School</a></td><td>Summer</td></tr>
        </table>"""
        client = SimpleNamespace(get=lambda url: SimpleNamespace(
            text=html, raise_for_status=lambda: None,
        ))
        urls = hku_catalog_search(client, self.request)
        self.assertIn("https://www.example.edu/summer", urls)
        self.assertNotIn("https://www.example.edu/winter", urls)
        self.assertNotIn("https://other.edu/summer", urls)

    def test_deepseek_key_alone_selects_browser_backend(self):
        request = self.request.model_copy(update={"programme_url": None})
        with patch.dict("os.environ", {
            "PATHLOOM_LLM_PROVIDER": "deepseek", "PATHLOOM_SEARCH_PROVIDER": "auto",
            "DEEPSEEK_API_KEY": "test-key", "BRAVE_API_KEY": "", "OPENAI_API_KEY": "",
        }), patch("research.deepseek_browser_search", return_value=["https://www.example.edu/summer"]), \
             patch("research.hku_catalog_search", return_value=[]), \
             patch("research.read_page", return_value=self.page), \
             patch("research.extract_with_ai", return_value=Draft()):
            response = TestClient(app).post("/api/research?provider=deepseek", json=request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("search:browser", response.json()["provider"])

    def test_decodes_bing_result_url(self):
        import base64
        target = "https://www.example.edu/summer"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        self.assertEqual(result_url(f"https://www.bing.com/ck/a?u=a1{encoded}"), target)

    def test_social_quote_cannot_verify_current_requirements(self):
        source = self.page.source.model_copy(update={"id": "SOC1", "publisher_type": "social",
            "url": "https://www.xiaohongshu.com/explore/abc"})
        social = Page(source=source, text=self.page.text, links={})
        quote = Evidence(source_id="SOC1", quote="Applications close on 1 March 2027")
        draft = Draft(programmes=[Programme(id="p1", name="Summer", programme_type="summer_school",
            destination="United Kingdom", official_url=source.url,
            past_cases=[Fact(value="A student's application experience", evidence=[quote])],
            steps=[Step(id="apply", title="Host application", category="host_application",
                action=Fact(value="Apply online", evidence=[Evidence(source_id="SOC1", quote="Apply online")]),
                deadline=Deadline(kind="fixed", date="2027-03-01", evidence=[quote]),
                documents=[DocumentRequirement(name="Transcript", evidence=[
                    Evidence(source_id="SOC1", quote="Upload a transcript")])])])])
        programmes, _ = verify_draft(draft, self.request, [social])
        self.assertIsNone(programmes[0].official_url)
        self.assertEqual(programmes[0].steps[0].deadline.state, "needs_review")
        self.assertEqual(programmes[0].steps[0].action.state, "needs_review")
        self.assertEqual(programmes[0].steps[0].documents[0].state, "needs_review")
        self.assertEqual(programmes[0].past_cases[0].state, "historical")

    def test_detail_crawl_allocates_links_to_every_host(self):
        pages = []
        for host in ("first.edu", "second.edu"):
            source = self.page.source.model_copy(update={"url": f"https://{host}/summer"})
            links = {label: f"https://{host}/summer/{label}" for label in (
                "application", "requirements", "fees", "housing")}
            pages.append(Page(source=source, text=self.page.text, links=links))
        urls = linked_detail_urls(pages)
        self.assertEqual(len(urls), 8)
        self.assertIn("first.edu", urls[0])
        self.assertIn("second.edu", urls[1])

    def test_gap_search_covers_both_selected_programmes(self):
        programmes = [Programme(id=host, name=f"{host} Summer School", programme_type="summer_school",
            destination="United Kingdom", official_url=f"https://{host}/summer")
            for host in ("first.edu", "second.edu")]
        queries = follow_up_queries(self.request, programmes)
        self.assertTrue(any("site:first.edu" in query and "required documents" in query for query in queries))
        self.assertTrue(any("site:second.edu" in query and "required documents" in query for query in queries))

    def test_one_host_cannot_use_all_initial_discovery_slots(self):
        discovered = [f"https://first.edu/{path}" for path in ("summer", "apply", "fees", "housing")]
        discovered.append("https://second.edu/summer")
        urls = prioritize_urls(self.request.model_copy(update={"programme_url": None}), discovered)
        self.assertIn("https://second.edu/summer", urls)
        self.assertNotIn("https://first.edu/housing", urls)

    def test_browser_block_is_recorded_as_failure_not_no_opportunity(self):
        search_log = []
        with patch("research.browser_search_query", side_effect=RuntimeError("CAPTCHA or inaccessible site")):
            hits = run_browser_search("site:xiaohongshu.com HKU 暑校", "social_discovery", search_log)
        self.assertEqual(hits, [])
        self.assertEqual(search_log[0].status, "error")
        self.assertIn("CAPTCHA", search_log[0].note)

    def test_repetitive_application_text_does_not_hide_late_fee_section(self):
        page = Page(source=self.page.source, links={},
            text=normalize("Application navigation repeated. " * 700 + " Tuition fee: GBP 2500. Required documents: a transcript."))
        excerpt = page_excerpt(page)
        self.assertIn("gbp 2500", excerpt)
        self.assertIn("required documents", excerpt)

    def test_public_accordion_data_preserves_requirements_and_application_links(self):
        data = json.dumps({"accordion": {"body": '<p>Minimum GPA 3.3. Upload a transcript.</p><p><a href="/summer/apply">Apply online</a></p>'}})
        html = '<html><title>Summer school</title><body><main>' + 'Programme introduction. ' * 10 + \
            '<a href="#main">Skip to main</a></main><script>window.REDUX_DATA = ' + data + ';</script></body></html>'
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html,
            headers={"content-type": "text/html"}, request=request))
        with httpx.Client(transport=transport) as client, patch("research.safe_public_url", side_effect=lambda url: url):
            page = read_page(client, "https://first.edu/summer", "S1")
        self.assertIn("gpa 3.3", page.text)
        self.assertIn("upload a transcript", page.text)
        self.assertIn("https://first.edu/summer/apply", page.links.values())
        self.assertFalse(any("#main" in url for url in linked_detail_urls([page])))


if __name__ == "__main__":
    unittest.main()
