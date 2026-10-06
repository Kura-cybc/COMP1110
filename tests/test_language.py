import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from models import Evidence, Fact, Programme, ResearchRequest, Source, Step
from research import Page, extract_with_ai, normalize, translate_display_text


class LanguageTests(unittest.TestCase):
    def request(self, language="zh"):
        return ResearchRequest(destination="United Kingdom", start_date="2027-06-01", end_date="2027-08-31",
                               programme_type="summer_school", language=language)

    def test_request_language_defaults_and_validation(self):
        payload = self.request().model_dump(mode="json")
        payload.pop("language")
        self.assertEqual(ResearchRequest.model_validate(payload).language, "zh")
        payload["language"] = "fr"
        response = TestClient(app).post("/api/research", json=payload)
        self.assertEqual(response.status_code, 422)

    def test_model_prompt_uses_selected_language(self):
        for language, expected in [("zh", "Chinese"), ("en", "English")]:
            calls = []
            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"programmes": []}'))])
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with patch("research.OpenAI", return_value=client):
                extract_with_ai(self.request(language), [], "deepseek", "test", "fake")
            prompt = calls[0]["messages"][1]["content"]
            self.assertIn(f"Answer in {expected}", prompt)
            self.assertIn("exact original-language evidence quotes", prompt)

    def test_translation_preserves_urls_numbers_evidence_and_states(self):
        quote = "原始证据：截止日期 2027-06-15。"
        step = Step(id="hku", category="hku_application", title="校内申请",
            action=Fact(value="申请截止 2027-06-15，费用 2,000。", state="needs_review",
                        evidence=[Evidence(source_id="KB1", quote=quote)]),
            channel=Fact(value="参考入口：[申请表](https://example.edu/Application(2027).pdf)", state="needs_review"),
            notes=["参考数据：" + ", ".join(str(number) for number in range(20))])
        programme = Programme(id="p1", name="Official Name", programme_type="summer_school", destination="UK", steps=[step])
        warnings = ["未知截止，联系 goabroad@hku.hk"]
        def create(**kwargs):
            inputs = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            translations = ["English guidance: " + text for text in inputs]
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                message=SimpleNamespace(content=json.dumps({"translations": translations})))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch("research.OpenAI", return_value=client):
            translate_display_text([programme], warnings, "deepseek", "test", "fake")
        self.assertTrue(step.action.value.startswith("English guidance:"))
        self.assertIn("2027-06-15", step.action.value)
        self.assertIn("2,000", step.action.value)
        self.assertIn("https://example.edu/Application(2027).pdf", step.channel.value)
        self.assertIn("goabroad@hku.hk", warnings[0])
        self.assertEqual(step.notes[0].split(": ", 1)[1], "参考数据：" + ", ".join(str(number) for number in range(20)))
        self.assertNotIn("__KEEP_", programme.model_dump_json())
        self.assertEqual(step.action.evidence[0].quote, quote)
        self.assertEqual(step.action.state, "needs_review")
        self.assertEqual(step.category, "hku_application")

    def test_unsafe_translation_keeps_original_batch(self):
        programme = Programme(id="p1", name="Test", programme_type="summer_school", destination="UK",
            steps=[Step(id="hku", category="hku_application", title="申请 2027")])
        before = programme.model_dump_json()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"translations": ["changed 2028"]}'))]))))
        warnings = []
        with patch("research.OpenAI", return_value=client):
            translate_display_text([programme], warnings, "deepseek", "test", "fake")
        self.assertEqual(programme.model_dump_json(), before)
        self.assertIn("could not be translated safely", warnings[-1])

    def test_english_pipeline_runs_display_translation(self):
        request = ResearchRequest.model_validate({**self.request("en").model_dump(mode="json"),
                                                 "programme_url": "https://example.edu/summer"})
        programme = Programme(id="p1", name="Test", programme_type="summer_school", destination="UK")
        def read(_client, url, source_id):
            return Page(source=Source(id=source_id, url=url, title="Programme", publisher_type="host",
                retrieved_at=datetime.now(timezone.utc), read_status="read"), text=normalize("Apply online."), links={})
        from models import Draft
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake", "EXCHANGEPREP_SEARCH_PROVIDER": "manual"}), \
             patch("research.read_page", side_effect=read), \
             patch("research.extract_with_ai", return_value=Draft(programmes=[programme])), \
             patch("research.translate_display_text") as translate:
            response = TestClient(app).post("/api/research?provider=deepseek", json=request.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["request"]["language"], "en")
        translate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
