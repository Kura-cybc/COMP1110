import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from knowledge_base import request_knowledge
from models import Deadline, DocumentRequirement, Draft, Fact, Programme, ResearchRequest, Source, Step
from research import Page, extract_with_ai, fill_review_content, normalize, verify_draft


class ReviewContentTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest(destination="United Kingdom", start_date=date(2027, 6, 1),
            end_date=date(2027, 8, 31), programme_type="summer_school",
            programme_url="https://example.edu/summer")
        self.snippets = request_knowledge(self.request)
        self.pages = [Page(source=Source(id=snippet.id, url="/api/knowledge-base/document",
            title=snippet.title, publisher_type="knowledge_base", retrieved_at=datetime.now(timezone.utc),
            read_status="read"), text=normalize(snippet.text), links=snippet.links) for snippet in self.snippets]

    def programme(self, **kwargs):
        return Programme(id="p1", name="Example Summer", official_url="https://example.edu/summer",
                         programme_type="summer_school", destination="United Kingdom", **kwargs)

    def test_empty_steps_get_grounded_content_and_clickable_entry(self):
        programmes, _ = verify_draft(Draft(programmes=[self.programme()]), self.request, self.pages)
        fill_review_content(programmes, self.request, self.pages, self.snippets)
        steps = {step.category: step for step in programmes[0].steps}
        visa = steps["visa"]
        self.assertTrue(visa.action.value)
        self.assertIn("gov.uk", visa.channel.value)
        self.assertIn("](", visa.channel.value)
        self.assertTrue(visa.action.evidence)
        self.assertTrue(visa.documents[0].detail)
        self.assertIsNone(visa.deadline.date)
        self.assertEqual(visa.action.state, "needs_review")
        self.assertEqual(visa.deadline.state, "needs_review")
        self.assertTrue(any("国籍" in note for note in visa.notes))
        self.assertIn("2027-06-15", steps["hku_application"].deadline.raw)
        self.assertIn("qualtrics.com", steps["hku_application"].channel.value)
        self.assertIn("2.0", steps["funding"].action.value)
        by_id = {page.source.id: page for page in self.pages}
        for step in steps.values():
            for item in (step.action, step.deadline, *step.documents):
                for evidence in item.evidence:
                    self.assertIn(normalize(evidence.quote), by_id[evidence.source_id].text)

    def test_known_values_are_not_overwritten(self):
        step = Step(id="visa", category="visa", title="Visa",
                    action=Fact(value="Confirmed operation", state="verified"),
                    channel=Fact(value="https://www.gov.uk/student-visa/apply-online", state="verified"))
        programme = self.programme(steps=[step])
        fill_review_content([programme], self.request, self.pages, self.snippets)
        self.assertEqual(step.action.value, "Confirmed operation")
        self.assertEqual(step.action.state, "verified")
        self.assertEqual(step.channel.state, "verified")

    def test_no_sources_gives_next_actions_not_invented_requirements(self):
        step = Step(id="visa", category="visa", title="签证办理")
        programme = self.programme(steps=[step])
        fill_review_content([programme], self.request, [], [])
        self.assertIn("google.com/search", step.channel.value)
        self.assertIn("不是申请入口", step.channel.value)
        self.assertFalse(step.action.evidence)
        self.assertIsNone(step.deadline.date)
        self.assertIn("获取适用材料", step.documents[0].name)

    def test_other_country_and_other_host_cannot_supply_requirements(self):
        japan = Page(source=Source(id="KBJP", url="/api/knowledge-base/document", title="6. 签证 / 6.3 日本",
            publisher_type="knowledge_base", retrieved_at=datetime.now(timezone.utc), read_status="read"),
            text=normalize("日本签证申请必须递交 Japan-only document"), links={"申请": "https://www.mofa.go.jp/"})
        other_host = Page(source=Source(id="S2", url="https://other.edu/apply", title="Other university",
            publisher_type="host", retrieved_at=datetime.now(timezone.utc), read_status="read"),
            text=normalize("Apply by 1 January 2027. Upload other-only transcript."), links={})
        programme = self.programme(steps=[Step(id="visa", category="visa", title="Visa"),
                                        Step(id="host", category="host_application", title="Apply")])
        fill_review_content([programme], self.request, [japan, other_host], [])
        serialized = programme.model_dump_json()
        self.assertNotIn("Japan-only", serialized)
        self.assertNotIn("mofa.go.jp", serialized)
        self.assertNotIn("other-only", serialized)
        self.assertNotIn("other.edu", serialized)

    def test_live_page_can_supply_reference_when_kb_has_no_match(self):
        page = Page(source=Source(id="SG", url="https://www.gov.uk/student-visa", title="Student visa",
            publisher_type="government", retrieved_at=datetime.now(timezone.utc), read_status="read"),
            text=normalize("Read this guidance. Apply online with passport and proof of admission. Apply before travel."),
            links={"Apply online": "https://www.gov.uk/student-visa/apply-online"})
        programme = self.programme(steps=[Step(id="visa", category="visa", title="Visa")])
        fill_review_content([programme], self.request, [page], [])
        self.assertIn("passport", programme.steps[0].action.value)
        self.assertEqual(programme.steps[0].action.evidence[0].source_id, "SG")
        self.assertEqual(programme.steps[0].action.state, "needs_review")

    def test_unsupported_model_claims_are_replaced_not_merely_relabelled(self):
        step = Step(id="visa", category="visa", title="Visa",
            action=Fact(value="Invented visa operation", state="needs_review"),
            channel=Fact(value="https://invented.example/visa", state="needs_review"),
            deadline=Deadline(kind="fixed", date="2027-01-01", raw="Invented date", state="needs_review"),
            documents=[DocumentRequirement(name="Invented required document", state="needs_review")])
        programme = self.programme(steps=[step])
        fill_review_content([programme], self.request, self.pages, self.snippets)
        self.assertNotIn("Invented", programme.model_dump_json())
        self.assertNotIn("invented.example", step.channel.value)
        self.assertIsNone(step.deadline.date)
        self.assertTrue(step.action.evidence)

    def test_winter_deadline_does_not_use_summer_registration(self):
        request = self.request.model_copy(update={"programme_type": "winter_school", "start_date": date(2027, 1, 1)})
        step = Step(id="hku", category="hku_application", title="HKU 登记")
        programme = self.programme(steps=[step])
        fill_review_content([programme], request, self.pages, self.snippets)
        self.assertIn("2026-12-15", step.deadline.raw)
        self.assertNotIn("2027-06-15", step.deadline.raw)

    def test_full_pipeline_returns_reference_content(self):
        def read(_client, url, source_id):
            return Page(source=Source(id=source_id, url=url, title="Project",
                publisher_type="host", retrieved_at=datetime.now(timezone.utc), read_status="read"),
                text=normalize("A summer programme. Apply online."), links={})
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake", "EXCHANGEPREP_SEARCH_PROVIDER": "manual"}), \
             patch("research.read_page", side_effect=read), \
             patch("research.extract_with_ai", return_value=Draft(programmes=[self.programme()])):
            response = TestClient(app).post("/api/research?provider=deepseek", json=self.request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        visa = next(step for step in response.json()["programmes"][0]["steps"] if step["category"] == "visa")
        self.assertTrue(visa["action"]["value"])
        self.assertIn("gov.uk", visa["channel"]["value"])
        self.assertEqual(visa["action"]["state"], "needs_review")

    def model_client(self, choices):
        iterator = iter(choices)
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[next(iterator)])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls

    def test_malformed_or_truncated_json_is_retried_once(self):
        for content, reason in [('{"programmes": [] "bad": true}', "stop"), ('{"programmes": []}', "length")]:
            with self.subTest(reason=reason):
                client, calls = self.model_client([
                    SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=reason),
                    SimpleNamespace(message=SimpleNamespace(content='{"programmes": []}'), finish_reason="stop")])
                with patch("research.OpenAI", return_value=client):
                    result = extract_with_ai(self.request, [], "deepseek", "test-model", "fake")
                self.assertEqual(result.programmes, [])
                self.assertEqual(len(calls), 2)

    def test_repeated_invalid_json_has_safe_error(self):
        client, calls = self.model_client([SimpleNamespace(message=SimpleNamespace(content='private-invalid-data'))] * 2)
        with patch("research.OpenAI", return_value=client), self.assertRaisesRegex(ValueError, "连续两次解析失败") as error:
            extract_with_ai(self.request, [], "deepseek", "test-model", "fake")
        self.assertNotIn("private-invalid-data", str(error.exception))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
