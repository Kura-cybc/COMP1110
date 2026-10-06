"""Local, isolated browser checks; no real searches, API calls or user browser session."""
import importlib.util
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from knowledge_base import request_knowledge
from models import Programme, ResearchRequest, ResearchResult, Source, Step
from research import Page, fill_review_content, normalize
from datetime import datetime, timezone


EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


@unittest.skipUnless(EDGE.is_file() and importlib.util.find_spec("playwright"), "Local Edge/Playwright unavailable")
class ReviewUITests(unittest.TestCase):
    def test_language_switch_and_research_request_in_browser(self):
        from playwright.sync_api import sync_playwright
        html = TestClient(app).get("/").text
        requests = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def route(request_route):
                    url = request_route.request.url
                    if url.endswith("/api/config"):
                        request_route.fulfill(json={"llm_provider": "deepseek", "deepseek_configured": True,
                                                  "search_provider": "browser"})
                    elif "/api/research?" in url:
                        payload = request_route.request.post_data_json
                        requests.append(payload)
                        request_route.fulfill(json={"request": payload, "provider": "offline-test", "sources": [],
                            "programmes": [{"id":"p1", "name":"English programme", "programme_type":"summer_school",
                                "dates":{"value":"June to August", "state":"needs_review"},
                                "steps":[{"id":"host", "title":"Apply to the host", "category":"host_application",
                                    "action":{"value":"Submit your application online.", "state":"needs_review"},
                                    "channel":{"value":"https://example.edu/apply", "state":"needs_review"},
                                    "deadline":{"kind":"unknown", "raw":"Confirm the current cycle", "state":"needs_review"}}]}]})
                    elif url.endswith("/"):
                        request_route.fulfill(body=html, content_type="text/html")
                    else:
                        request_route.abort()
                page.route("**/*", route)
                page.goto("http://exchange.test/")
                self.assertEqual(page.locator("h1").inner_text(), "把申请要求查清楚")
                page.locator('[name="interests"]').fill("My saved interest")
                page.locator("#language-button").click()
                self.assertEqual(page.locator("h1").inner_text(), "Make application requirements clear")
                self.assertEqual(page.locator('html').get_attribute('lang'), "en")
                self.assertEqual(page.locator("#run-button").inner_text(), "Start online research")
                self.assertEqual(page.locator('[name="interests"]').input_value(), "My saved interest")
                self.assertEqual(page.locator('[name="programme_type"]').input_value(), "summer_school")
                page.reload()
                self.assertEqual(page.locator("#language-button").inner_text(), "中文")
                self.assertEqual(page.locator("h1").inner_text(), "Make application requirements clear")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready:')")
                page.locator("#run-button").click()
                page.wait_for_selector(".step")
                self.assertEqual(requests[-1]["language"], "en")
                self.assertEqual(page.locator('.step .row strong').first.text_content(), "What to do")
                self.assertIn("Note (needs review)", page.locator('.step .review-note').inner_text())
                self.assertEqual(page.locator('.step a').first.get_attribute('href'), "https://example.edu/apply")
                page.locator("#language-button").click()
                self.assertEqual(page.locator("h1").inner_text(), "把申请要求查清楚")
                self.assertEqual(page.locator('.step .row strong').first.inner_text(), "具体操作")
                self.assertIn("Submit your application online.", page.locator('.step').inner_text())
                self.assertEqual(len(requests), 1)  # Toggling does not make hidden translation/research calls.
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_reference_cards_and_inline_links_in_browser(self):
        from playwright.sync_api import sync_playwright

        request = ResearchRequest(destination="United Kingdom", start_date="2027-06-01",
                                  end_date="2027-08-31", programme_type="summer_school")
        snippets = request_knowledge(request)
        pages = [Page(source=Source(id=s.id, url="/api/knowledge-base/document", title=s.title,
            publisher_type="knowledge_base", retrieved_at=datetime.now(timezone.utc), read_status="read"),
            text=normalize(s.text), links=s.links) for s in snippets]
        programme = Programme(id="p1", name="UI reference test", programme_type="summer_school", destination="UK",
                              steps=[Step(id="visa", category="visa", title="签证参考")])
        fill_review_content([programme], request, pages, snippets)
        result = ResearchResult(request=request, generated_at=datetime.now(timezone.utc), provider="offline-test",
            programmes=[programme], sources=[p.source for p in pages], knowledge_matches=snippets)
        html = TestClient(app).get("/").text
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def route(request_route):
                    if request_route.request.url.endswith("/api/config"):
                        request_route.fulfill(json={"llm_provider": "deepseek", "deepseek_configured": True,
                                                  "search_provider": "browser"})
                    elif request_route.request.url.endswith("/"):
                        request_route.fulfill(body=html, content_type="text/html")
                    else:
                        request_route.abort()
                page.route("**/*", route)
                page.goto("http://exchange.test/")
                page.evaluate("data => render(data)", result.model_dump(mode="json"))
                card = page.locator(".step")
                self.assertIn("参考流程", card.inner_text())
                self.assertGreater(card.locator('a[href*="gov.uk"]').count(), 0)
                self.assertEqual(card.locator(".tag.needs_review, .tag.unknown").count(), 0)
                self.assertEqual(card.locator(".review-note").count(), 1)
                self.assertIn("备注（待核实）", card.locator(".review-note").inner_text())
                self.assertNotIn("实际提交渠道待核实", card.inner_text())
                self.assertNotIn("不是已确认必交清单", card.inner_text())
                self.assertNotIn("不能直接排期：", card.inner_text())
                self.assertEqual(programme.steps[0].action.state, "needs_review")
                self.assertNotIn("unknown（待核实）", card.inner_text())
                self.assertGreater(page.locator('pre a[href*="gov.uk"]').count(), 0)
                # Exercise the same text rendering used by fields, notes, warnings and the knowledge base.
                rendered = page.evaluate("""() => {
                  const node = linkedEl('div', '入口：[申请表](https://example.edu/Application(2027).pdf)；另见 https://example.edu/apply。联系 goabroad@hku.hk；<img src=x onerror=alert(1)>');
                  document.body.appendChild(node);
                  const unsafe = link('javascript:alert(1)', 'unsafe');
                  return {links: [...node.querySelectorAll('a')].map(a => ({href:a.getAttribute('href'),label:a.textContent})),
                          images:node.querySelectorAll('img').length, unsafeTag:unsafe.tagName};
                }""")
                self.assertIn({"href": "https://example.edu/Application(2027).pdf", "label": "申请表"}, rendered["links"])
                self.assertIn({"href": "https://example.edu/apply", "label": "https://example.edu/apply"}, rendered["links"])
                self.assertIn({"href": "mailto:goabroad@hku.hk", "label": "goabroad@hku.hk"}, rendered["links"])
                self.assertEqual(rendered["images"], 0)
                self.assertEqual(rendered["unsafeTag"], "SPAN")
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
