"""Generate a provisional, source-cited VFR CAR benchmark for human review."""

import argparse
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from ingestion.tc_car_xml.parse import CAR_XML, parse_document

load_dotenv()

BENCHMARK_PATH = Path("eval/car_test_set.json")
GENERATOR_MODEL = "gpt-5.4-mini"
QUESTION_COUNT = 250
SECTIONS = """
401.03 401.05 401.08 401.12 401.19 401.22 401.26 401.28 401.30 401.32 401.34
601.03 601.04 601.06 601.07 601.08 601.09 601.15 601.17 601.18
602.01.1 602.01.2 602.02 602.03 602.04 602.05 602.07 602.08 602.09
602.10 602.11 602.12 602.13 602.14 602.15 602.17 602.19 602.20 602.21
602.22 602.23 602.24 602.27 602.28 602.31 602.32 602.34 602.35 602.36
602.37 602.40 602.41 602.60 602.61 602.62 602.63 602.65 602.71 602.72
602.73 602.74 602.75 602.76 602.77 602.78 602.79 602.86 602.87 602.88
602.89 602.96 602.97 602.98 602.99 602.100 602.101 602.102 602.103
602.114 602.115 602.116 602.117 602.133 602.136 602.138 602.143
605.03 605.03.1 605.04 605.05 605.06 605.07 605.08 605.09 605.10
605.14 605.15 605.16 605.17 605.22 605.23 605.24 605.25 605.26 605.27
605.28 605.29 605.30 605.31 605.32 605.35 605.38 605.39 605.40
605.84 605.85 605.86 605.88 605.92 605.93 605.94 605.95 605.97
""".split()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def source_text(document):
    return "\n".join(line for line, _ in document["lines"])


def validate_questions(items, document, count):
    if not isinstance(items, list) or len(items) != count:
        raise ValueError(f"{document['section']}: expected {count} questions")
    source = normalize(source_text(document))
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{document['section']}: question is not an object")
        question, answer, quote = (item.get(key) for key in ("question", "ground_truth", "evidence_quote"))
        if not all(isinstance(value, str) and value.strip() for value in (question, answer, quote)):
            raise ValueError(f"{document['section']}: question, answer, and quote are required")
        normalized_quote = normalize(quote)
        match = re.search(re.escape(normalized_quote), source, re.IGNORECASE)
        if len(normalized_quote) < 20 or not match:
            raise ValueError(f"{document['section']}: evidence quote is not verbatim source text")
        result.append({
            "question": question.strip(), "ground_truth": answer.strip(),
            "section": document["section"], "source_url": document["source_url"],
            "evidence_quote": match.group(), "reviewed": False,
        })
    return result


def generate_for_section(document, count, client):
    source = source_text(document)
    prompt = (
        f"Create exactly {count} distinct, practical questions for a Canadian VFR private pilot "
        "using only the CAR provision below. Return JSON with an 'items' array. Each item must "
        "contain 'question', 'ground_truth', and 'evidence_quote'. The evidence quote must be a "
        "contiguous verbatim excerpt from a single line of this provision, at least 20 characters "
        "long. Copy it exactly, with no ellipses or added words. Answers "
        "must be concise, directly supported by that quote and the provision, and preserve "
        "applicability, limitations and exceptions. Avoid requiring other sections, standards, "
        "interpretation, inferred assumptions, or outside knowledge. Vary question types. "
        "Do not ask multiple versions of the same question.\n\n"
        f"CAR {document['section']} — {document['title']}\n{source}"
    )
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(3):
        response = client.chat.completions.create(
            model=GENERATOR_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            max_completion_tokens=5000,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError(f"{document['section']}: benchmark generation returned no content")
        try:
            return validate_questions(json.loads(content).get("items"), document, count)
        except (ValueError, json.JSONDecodeError) as error:
            if attempt == 2:
                raise ValueError(f"{document['section']}: invalid benchmark output after three attempts") from error
            messages.extend([
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"Invalid output: {error}. Return corrected JSON; copy evidence exactly."},
            ])


def generate_benchmark(path=BENCHMARK_PATH, xml=CAR_XML, total=QUESTION_COUNT, workers=4):
    if total < 1 or workers < 1:
        raise ValueError("total and workers must be positive")
    documents = {d["section"]: d for d in parse_document(xml) if d["kind"] == "section"}
    missing = [section for section in SECTIONS if section not in documents]
    if missing:
        raise ValueError(f"Sections missing from XML: {', '.join(missing)}")
    selected = SECTIONS[:min(total, len(SECTIONS))]
    counts = {section: total // len(selected) + (i < total % len(selected))
              for i, section in enumerate(selected)}
    manifest = {
        "schema_version": 1, "source": "TC_CAR", "xml_sha256": file_hash(xml),
        "point_in_time": documents[selected[0]]["point_in_time"], "generator": GENERATOR_MODEL,
        "target_count": total, "scope": "VFR pilot and flight operations",
        "review_status": "machine-generated; human review required",
    }
    path = Path(path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("manifest") != manifest:
            raise ValueError(f"{path} belongs to a different benchmark; choose a new output path")
    else:
        data = {"manifest": manifest, "sections": {}}
    for section, items in data["sections"].items():
        if section not in counts:
            raise ValueError(f"Unexpected section {section} in benchmark")
        validate_questions(items, documents[section], counts[section])

    client = OpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/",
        timeout=120,
    )
    remaining = [section for section in selected if section not in data["sections"]]
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(generate_for_section, documents[s], counts[s], client): s
                   for s in remaining}
        for future in as_completed(futures):
            section = futures[future]
            try:
                data["sections"][section] = future.result()
            except Exception as error:
                failures.append((section, error))
                print(f"Failed to generate {section}: {error}", flush=True)
                continue
            write_json(path, data)
            print(f"Generated {sum(len(items) for items in data['sections'].values())}/{total} questions", flush=True)

    if failures:
        raise RuntimeError(f"Benchmark generation failed for {len(failures)} sections; rerun to resume") from failures[0][1]
    questions = [item["question"].casefold() for section in selected for item in data["sections"][section]]
    if len(questions) != total or len(set(questions)) != total:
        raise ValueError("Benchmark has missing or duplicate questions; review before evaluation")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=BENCHMARK_PATH)
    parser.add_argument("--xml", default=CAR_XML)
    parser.add_argument("--count", type=int, default=QUESTION_COUNT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    generate_benchmark(args.output, args.xml, args.count, args.workers)


if __name__ == "__main__":
    main()
