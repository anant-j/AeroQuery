import re


def estimate_tokens(text):
    """Rough token estimate: ~1 token per 4 characters for English."""
    return len(text) // 4


def extract_sections(full_text):
    """Split cleaned text into sections based on numbered headings like 1.2, 8.4.4."""
    section_pattern = re.compile(
        r"^(\d+\.\d+(?:\.\d+)*)\s+(.+)", re.MULTILINE
    )

    matches = list(section_pattern.finditer(full_text))

    if not matches:
        return [{"section": "unknown", "title": "", "text": full_text}]

    sections = []
    for i, match in enumerate(matches):
        section_num = match.group(1)
        title = match.group(2).split("\n")[0].strip()

        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        section_text = full_text[start:end].strip()

        sections.append({
            "section": section_num,
            "title": title,
            "text": section_text,
        })

    return sections


def chunk_section(section, max_tokens=512, overlap_tokens=50):
    """Split a single section into chunks that fit within max_tokens."""
    header = f"[{section['section']}] {section['title']}"
    text = section["text"]
    tokens = estimate_tokens(text)

    base_metadata = {
        "source": "CANADA_AIM",
        "section": section["section"],
        "title": section["title"],
    }

    # Small enough — return as one chunk
    if tokens <= max_tokens:
        return [{
            "text": text,
            "metadata": base_metadata,
        }]

    # Large section — split by paragraphs with overlap
    paragraphs = text.split("\n")
    chunks = []
    current_lines = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        if current_tokens + para_tokens > max_tokens and current_lines:
            chunk_text = header + "\n" + "\n".join(current_lines)
            chunks.append({
                "text": chunk_text,
                "metadata": {**base_metadata, "chunk_part": len(chunks) + 1},
            })

            # Keep last lines as overlap
            overlap_lines = []
            overlap_count = 0
            for line in reversed(current_lines):
                line_tokens = estimate_tokens(line)
                if overlap_count + line_tokens > overlap_tokens:
                    break
                overlap_lines.insert(0, line)
                overlap_count += line_tokens

            current_lines = overlap_lines
            current_tokens = overlap_count

        current_lines.append(para)
        current_tokens += para_tokens

    # Final chunk
    if current_lines:
        chunk_text = header + "\n" + "\n".join(current_lines)
        chunks.append({
            "text": chunk_text,
            "metadata": {**base_metadata, "chunk_part": len(chunks) + 1},
        })

    return chunks


def chunk_document(full_text, max_tokens=512, overlap_tokens=50, min_tokens=20):
    """Full pipeline: extract sections then chunk each one."""
    sections = extract_sections(full_text)
    all_chunks = []

    for section in sections:
        # Skip TOC-like entries (lines full of dots)
        if "..........." in section["title"]:
            continue

        chunks = chunk_section(section, max_tokens, overlap_tokens)
        all_chunks.extend(chunks)

    # Drop chunks below minimum token threshold
    all_chunks = [c for c in all_chunks if estimate_tokens(c["text"]) >= min_tokens]

    return all_chunks
