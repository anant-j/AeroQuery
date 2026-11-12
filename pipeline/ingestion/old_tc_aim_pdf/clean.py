import re


def clean_canada_page(text):
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        stripped = line.strip()

        # Remove header: "TC AIM March 19, 2026" or "March 19, 2026 TC AIM"
        if re.match(r"^(TC AIM\s+\w+ \d{1,2}, \d{4}|\w+ \d{1,2}, \d{4}\s+TC AIM)$", stripped):
            continue

        # Remove standalone page numbers
        if re.match(r"^\d{1,4}$", stripped):
            continue

        # Remove standalone chapter prefixes (GEN, AGA, COM, MET, RAC, etc.)
        if re.match(r"^(GEN|AGA|COM|MET|RAC|MAP|SAR|AIR|LRA|NAT)$", stripped):
            continue

        # Skip empty lines
        if not stripped:
            continue

        cleaned.append(stripped)

    return "\n".join(cleaned)
