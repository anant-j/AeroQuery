import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from ingestion.tc_car_xml.clean import clean_text, formula_text, inline_text, table_rows

CAR_XML = Path("data/raw/SOR-96-433.xml")
LIMS = "{http://justice.gc.ca/lims}"
SOURCE_URL = "https://laws-lois.justice.gc.ca/eng/regulations/SOR-96-433/"

# Historical notes are amendment history, not the provision itself.
IGNORED = {"HistoricalNote", "MarginalNote"}
INLINE = {"Text", "TitleText", "OriginatingRef", "Repealed", "AlternateText", "Caption", "FormulaConnector"}


def _lines(element, ancestors=()):
    """Yield (line, ancestor lines) for legal provisions and schedules."""
    if element.tag in IGNORED:
        return
    if element.tag == "TableGroup":
        for row in table_rows(element):
            yield row, ancestors
        return
    if element.tag == "ImageGroup":
        description = " ".join(
            inline_text(child) for child in element
            if child.tag in {"Caption", "AlternateText"}
        )
        yield f"[Image not included: {clean_text(description) or 'see source'}]", ancestors
        return
    if element.tag == "Formula":
        math = element.find("MathML")
        if math is None:
            raise ValueError("Formula has no MathML")
        yield f"[Formula, consult source for notation: {formula_text(math)}]", ancestors
        return
    if element.tag in INLINE:
        value = inline_text(element)
        if value:
            yield value, ancestors
        return

    label = clean_text(element.findtext("Label"))
    term = element.find("FormulaTerm")
    if term is not None:
        label = " ".join(filter(None, [label, inline_text(term)]))
    text_children = [child for child in element if child.tag == "Text"]
    own_text = " ".join(inline_text(child) for child in text_children)
    prefix = clean_text(" ".join(filter(None, [label, own_text])))
    if prefix:
        yield prefix, ancestors
    child_ancestors = (*ancestors, prefix) if prefix else ancestors
    for child in element:
        if child.tag in {"Label", "Text", "FormulaTerm"}:
            continue
        yield from _lines(child, child_ancestors)


def parse_document(path=CAR_XML):
    root = ET.parse(path).getroot()
    if root.tag != "Regulation" or root.findtext("Identification/InstrumentNumber") != "SOR/96-433":
        raise ValueError("Expected the Canadian Aviation Regulations XML (SOR/96-433)")
    body = root.find("Body")
    if body is None:
        raise ValueError("CAR XML does not contain a Body")
    version = root.get(f"{LIMS}pit-date")
    if not version:
        raise ValueError("CAR XML does not contain a point-in-time date")
    for element in body.iter():
        effective = element.get(f"{LIMS}inforce-start-date")
        if effective and effective > version:
            raise ValueError(f"Provision begins after the XML point-in-time date: {effective}")

    headings = {}
    documents = []
    for element in body:
        if element.tag == "Heading":
            level = int(element.get("level", "0"))
            if level < 1:
                raise ValueError("CAR heading has no valid level")
            headings = {key: value for key, value in headings.items() if key < level}
            headings[level] = clean_text(" ".join(
                inline_text(child) for child in element if child.tag in {"Label", "TitleText"}
            ))
        elif element.tag in {"Section", "Schedule"}:
            fid = element.get(f"{LIMS}fid")
            if not fid:
                raise ValueError(f"{element.tag} is missing its source identifier")
            if element.tag == "Section":
                section = clean_text(element.findtext("Label"))
                if not section:
                    raise ValueError("CAR section is missing its label")
                url = f"{SOURCE_URL}section-{quote(section, safe='.')}.html"
            else:
                section = clean_text(element.findtext("ScheduleFormHeading/Label"))
                if not section:
                    raise ValueError("CAR schedule is missing its label")
                url = f"{SOURCE_URL}section-sched{fid}.html"
            active_headings = headings if element.tag == "Section" else {
                key: value for key, value in headings.items() if key <= 2
            }
            if element.tag == "Schedule":
                schedule_heading = element.find("Heading/TitleText")
                title = inline_text(schedule_heading) if schedule_heading is not None else section
            else:
                title = active_headings[max(active_headings)] if active_headings else ""
            lines = list(_lines(element))
            if not lines:
                raise ValueError(f"No text extracted from {section}")
            documents.append({
                "id": fid,
                "section": section,
                "title": title,
                "hierarchy": " > ".join(active_headings.values()),
                "source": "TC_CAR",
                "source_url": url,
                "point_in_time": version,
                "kind": element.tag.lower(),
                "lines": lines,
            })
    if not documents:
        raise ValueError("No sections or schedules found in CAR XML")
    return documents
