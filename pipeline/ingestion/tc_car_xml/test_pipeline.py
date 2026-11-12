import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ingestion.tc_car_xml.chunk import chunk_document, token_count
from ingestion.tc_car_xml.clean import formula_text, inline_text
from ingestion.tc_car_xml.embed import embed_texts, run_ingestion
from ingestion.tc_car_xml.parse import parse_document


XML = """<?xml version="1.0"?>
<Regulation xmlns:lims="http://justice.gc.ca/lims" lims:pit-date="2026-06-17">
  <Identification><InstrumentNumber>SOR/96-433</InstrumentNumber></Identification>
  <Body>
    <Heading level="1"><Label>PART VI</Label><TitleText>Flight <Emphasis>Rules</Emphasis></TitleText></Heading>
    <Heading level="2"><TitleText>Fuel Requirements</TitleText></Heading>
    <Section lims:fid="123"><Label>602.88</Label>
      <Subsection><Label>(1)</Label><Text>This section does not apply to gliders.</Text></Subsection>
      <Subsection><Label>(2)</Label><Text>A pilot shall carry sufficient fuel</Text>
        <Paragraph><Label>(a)</Label><Text>during the day, <Emphasis>30 minutes</Emphasis> at cruise;</Text></Paragraph>
        <Paragraph><Label>(b)</Label><Text>during the night, 45 minutes.</Text></Paragraph>
        <FormulaGroup><Formula><MathML><math><mi>x</mi></math></MathML></Formula>
          <FormulaConnector>where</FormulaConnector>
          <FormulaDefinition><FormulaTerm>x</FormulaTerm><Text>is a quantity.</Text></FormulaDefinition>
        </FormulaGroup>
      </Subsection>
    </Section>
    <Schedule lims:fid="456"><ScheduleFormHeading><Label>SCHEDULE I</Label>
      <OriginatingRef>(Section 602.88)</OriginatingRef></ScheduleFormHeading>
      <ImageGroup><AlternateText>A diagram</AlternateText><Image source="missing.png"/></ImageGroup>
      <TableGroup><table><tgroup><thead><row><entry>Type</entry><entry>Time</entry></row></thead>
      <tbody><row><entry>Day</entry><entry>30</entry></row></tbody></tgroup></table></TableGroup>
    </Schedule>
  </Body>
  <RelatedOrNotInForce><Section lims:fid="789"><Label>999.99</Label><Text>Future text</Text></Section></RelatedOrNotInForce>
</Regulation>"""


class CarPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "car.xml"
        self.path.write_text(XML, encoding="utf-8")

    def test_parse_provisions_schedule_and_exclude_future_material(self):
        documents = parse_document(self.path)
        self.assertEqual([d["section"] for d in documents], ["602.88", "SCHEDULE I"])
        self.assertIn("Flight Rules", documents[0]["hierarchy"])
        chunks = chunk_document(documents)
        text = chunks[0]["text"]
        self.assertIn("(1) This section does not apply to gliders.", text)
        self.assertIn("(a) during the day, 30 minutes at cruise;", text)
        self.assertIn("[Formula, consult source for notation: x]", text)
        self.assertIn("x is a quantity.", text)
        self.assertIn("section-602.88.html", chunks[0]["metadata"]["source_url"])
        self.assertIn("Type | Time\nDay | 30", chunks[1]["text"])
        self.assertIn("[Image not included: A diagram]", chunks[1]["text"])
        self.assertIn("section-sched456.html", chunks[1]["metadata"]["source_url"])

    def test_splits_respect_token_limit_and_repeat_parent_context(self):
        document = parse_document(self.path)[0]
        document["lines"] = [
            ("602.88", ()),
            ("(2) A pilot shall carry sufficient fuel", ("602.88",)),
            ("(a) " + "fuel reserve requirements " * 120, ("602.88", "(2) A pilot shall carry sufficient fuel")),
        ]
        chunks = chunk_document([document], max_tokens=80)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(token_count(c["text"]) <= 80 for c in chunks))
        self.assertTrue(all("(2) A pilot shall carry sufficient fuel" in c["text"] for c in chunks))
        self.assertEqual(len({c["id"] for c in chunks}), len(chunks))
        self.assertEqual(sum(c["text"].count("requirements") for c in chunks), 120)

    def test_rejects_future_effective_content(self):
        self.path.write_text(XML.replace('<Section lims:fid="123">',
                                         '<Section lims:fid="123" lims:inforce-start-date="2027-01-01">'),
                             encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "after the XML point-in-time"):
            parse_document(self.path)

    def test_formula_preserves_subscripts_and_fraction_grouping(self):
        term = ET.fromstring("<FormulaTerm>M<Sub>f</Sub></FormulaTerm>")
        formula = ET.fromstring(
            "<mfrac><msub><mi>M</mi><mi>f</mi></msub><mi>n</mi></mfrac>"
        )
        self.assertEqual(inline_text(term), "M_(f)")
        self.assertEqual(formula_text(formula), "(M_(f)) / (n)")

    def test_embedding_response_must_match_batch(self):
        client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.0] * 3072)]
        )))
        with self.assertRaisesRegex(ValueError, "does not match"):
            embed_texts(["one", "two"], client, "model")

    def test_ingestion_stages_and_verifies_without_deleting_existing_vectors(self):
        namespace_counts = {"canada": SimpleNamespace(vector_count=10)}
        batches = []

        class Index:
            def describe_index_stats(self):
                return SimpleNamespace(dimension=3072, namespaces=namespace_counts)

            def upsert(self, vectors, namespace):
                batches.extend(vectors)
                namespace_counts[namespace] = SimpleNamespace(vector_count=len(batches))
                return SimpleNamespace(upserted_count=len(vectors))

        class Pinecone:
            def __init__(self, api_key):
                pass

            def Index(self, name):
                return Index()

        class OpenAI:
            def __init__(self, **kwargs):
                self.embeddings = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(data=[
                    SimpleNamespace(index=i, embedding=[0.0] * 3072)
                    for i in reversed(range(len(kwargs["input"])))
                ]))

        modules = {
            "dotenv": SimpleNamespace(load_dotenv=lambda: None),
            "openai": SimpleNamespace(OpenAI=OpenAI),
            "pinecone": SimpleNamespace(Pinecone=Pinecone),
        }
        env = {
            "AZURE_OPENAI_ENDPOINT": "https://example.test",
            "AZURE_OPENAI_API_KEY": "test",
            "PINECONE_API_KEY": "test",
            "PINECONE_INDEX_NAME": "test",
        }
        with patch.dict("sys.modules", modules), patch.dict(os.environ, env):
            run_ingestion(self.path, "car-test")
            with self.assertRaisesRegex(ValueError, "already exists"):
                run_ingestion(self.path, "canada")
        self.assertEqual(namespace_counts["canada"].vector_count, 10)
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0]["metadata"]["source"], "TC_CAR")


if __name__ == "__main__":
    unittest.main()
