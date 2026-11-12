import re


def clean_text(text):
    """Normalize whitespace without changing the words or punctuation."""
    return re.sub(r"\s+", " ", text or "").strip()


def inline_text(element):
    """Extract mixed XML content in document order, including child tails."""
    def render(node):
        pieces = [node.text or ""]
        for child in node:
            value = render(child)
            if child.tag == "Sub":
                value = f"_({value})"
            elif child.tag == "Sup":
                value = f"^({value})"
            pieces.extend([value, child.tail or ""])
        return "".join(pieces)

    return clean_text(render(element))


def formula_text(element):
    """Render the MathML constructs in the CAR XML as explicitly grouped text."""
    tag = element.tag
    children = list(element)
    if tag in {"mi", "mn", "mo"}:
        return clean_text(element.text)
    if tag == "mspace":
        return " "
    if tag in {"MathML", "math", "mrow"}:
        return " ".join(formula_text(child) for child in children)
    parts = [formula_text(child) for child in children]
    if tag == "msub" and len(parts) == 2:
        return f"{parts[0]}_({parts[1]})"
    if tag == "mfrac" and len(parts) == 2:
        return f"({parts[0]}) / ({parts[1]})"
    if tag == "munder" and len(parts) == 2:
        return f"{parts[0]}_({parts[1]})"
    if tag == "munderover" and len(parts) == 2:
        return f"{parts[0]}_({parts[1]})"
    if tag == "munderover" and len(parts) == 3:
        return f"{parts[0]}_({parts[1]})^({parts[2]})"
    raise ValueError(f"Unsupported MathML element: {tag}")


def table_rows(element):
    """Render table cells in order and repeat column headings with each row."""
    lines = []
    for group in element.iter("tgroup"):
        headers = []
        head = group.find("thead")
        if head is not None:
            for row in head.findall("row"):
                headers.append(" | ".join(inline_text(cell) for cell in row.findall("entry")))
        body = group.find("tbody")
        if body is not None:
            for row in body.findall("row"):
                values = " | ".join(inline_text(cell) for cell in row.findall("entry"))
                lines.append("\n".join(filter(None, [*headers, values])))
        elif headers:
            lines.extend(headers)
    if not lines:
        text = inline_text(element)
        if text:
            lines.append(text)
    return lines
