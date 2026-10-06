"""Retrieve small, attributed sections from the project's editable Markdown file."""

import re
from pathlib import Path
from urllib.parse import urldefrag

from models import KnowledgeSnippet, ResearchRequest


KNOWLEDGE_PATH = Path(__file__).resolve().parent / "hku_knowledge_base_template.md"
COUNTRIES = {
    "美国": ("united states", "usa", "us", "美国"),
    "加拿大": ("canada", "加拿大"),
    "日本": ("japan", "日本"),
    "韩国": ("south korea", "korea", "韩国"),
    "新加坡": ("singapore", "新加坡"),
    "英国": ("united kingdom", "uk", "britain", "英国"),
    "法国": ("france", "法国"),
    "德国": ("germany", "德国"),
    "西班牙": ("spain", "西班牙"),
    "澳大利亚": ("australia", "澳洲", "澳大利亚"),
}
TOPICS = {
    "学分": ("credit", "syllabus", "学分", "转分"),
    "证明": ("transcript", "testimonial", "document", "成绩单", "材料", "证明"),
    "资助": ("funding", "scholarship", "资助", "奖学金", "hsbc"),
    "LoA": ("leave", "loa", "defer", "gap", "休学", "延期"),
    "保险": ("insurance", "保险", "体检", "住宿", "出行"),
}


def terms_for(query: str) -> set[str]:
    lower = query.casefold()
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", lower))
    for country, aliases in COUNTRIES.items():
        if any(re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", lower) for alias in aliases):
            terms.add(country)
    for topic, aliases in TOPICS.items():
        if any(alias in lower for alias in aliases):
            terms.add(topic.casefold())
    return terms


def retrieve_knowledge(query: str, *, path: Path | None = None, limit: int = 8) -> list[KnowledgeSnippet]:
    """Headings keep faculty/cohort caveats with their facts; no vector DB or API key."""
    text = (path or KNOWLEDGE_PATH).read_text(encoding="utf-8")
    references = dict(re.findall(r"^\[([^\]]+)\]:\s*(https?://\S+)", text, re.M))
    terms = terms_for(query)
    if not terms:
        return []
    sections = []
    parent = ""
    parent_intro = ""
    for match in re.finditer(r"^(#{2,3}) (.+)\n([\s\S]*?)(?=^#{2,3} |\Z)", text, re.M):
        level, heading, body = match.groups()
        if level == "##":
            parent = heading
            parent_intro = body.strip()
        # The source appendix and TOC are navigation, not retrievable facts.
        if heading == "目录" or parent.startswith("8."):
            continue
        if not body.strip():
            continue
        title = heading if level == "##" else f"{parent} / {heading}"
        country_heading = re.match(r"6\.\d+ (\S+)", heading)
        if country_heading and country_heading.group(1) not in terms:
            continue
        if country_heading and parent_intro:
            body = parent_intro + "\n\n" + body
        lower = (heading + " " + body).casefold()
        score = sum(6 if term in heading.casefold() else 1 for term in terms if term in lower)
        if country_heading:
            score += 100
        if not score:
            continue
        # A balanced Markdown URL parser is needed for filenames containing parentheses.
        links = markdown_links(body)
        for reference in re.findall(r"\[([^\]]+)\](?![(:\[])|\]\[([^\]]+)\]", body):
            key = reference[0] or reference[1]
            if key in references:
                links[key] = references[key]
        body = re.sub(r'<a id="[^"]+"></a>\s*', "", body).strip()
        sections.append((score, KnowledgeSnippet(id=f"KB{len(sections) + 1}", title=title,
                                                text=body, links=links)))
    sections.sort(key=lambda item: item[0], reverse=True)
    selected, used = [], 0
    for _, section in sections:
        if len(selected) >= limit or used + len(section.text) > 20000:
            continue
        selected.append(section)
        used += len(section.text)
    return selected


def markdown_links(text: str) -> dict[str, str]:
    links = {}
    for match in re.finditer(r"\[([^\]]+)\]\((https?://)", text):
        start = match.end() - len(match.group(2))
        position, depth = start, 1
        while position < len(text) and depth:
            char = text[position]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char in "\r\n":
                break
            position += 1
        if depth == 0:
            label = match.group(1)
            url = urldefrag(text[start:position - 1])[0]
            if label in links and links[label] != url:
                label = f"{label} ({len(links) + 1})"
            links[label] = url
    return links


def request_knowledge(request: ResearchRequest) -> list[KnowledgeSnippet]:
    return retrieve_knowledge(f"{request.destination} {request.major} {request.interests} "
                              "学分 证明 资助 LoA 保险 交换")


def knowledge_urls(snippets: list[KnowledgeSnippet], limit: int = 8) -> list[str]:
    """One link per section per pass prevents a long table consuming the crawl budget."""
    buckets = []
    for snippet in snippets:
        urls = []
        for url in snippet.links.values():
            if "mp.weixin.qq.com" in url or url in urls:
                continue
            urls.append(url)
        buckets.append(urls)
    selected = []
    for index in range(max(map(len, buckets), default=0)):
        for bucket in buckets:
            if index < len(bucket) and bucket[index] not in selected:
                selected.append(bucket[index])
                if len(selected) >= limit:
                    return selected
    return selected
