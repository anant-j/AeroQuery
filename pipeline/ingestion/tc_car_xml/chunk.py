import tiktoken


ENCODING = tiktoken.get_encoding("cl100k_base")


def token_count(text):
    return len(ENCODING.encode(text, disallowed_special=()))


def _split_line(line, prefix, limit):
    """Split an oversized line at character boundaries without losing text."""
    while line:
        if token_count(prefix + line) <= limit:
            yield line
            return
        low, high = 1, len(line)
        while low < high:
            middle = (low + high + 1) // 2
            if token_count(prefix + line[:middle]) <= limit:
                low = middle
            else:
                high = middle - 1
        if token_count(prefix + line[:low]) > limit:
            raise ValueError("Chunk limit is too small for the required context")
        split = line.rfind(" ", 0, low)
        if split > 0:
            low = split + 1
        while low and token_count(prefix + line[:low]) > limit:
            low -= 1
        if not low:
            raise ValueError("Chunk limit is too small for the required context")
        yield line[:low]
        line = line[low:]


def chunk_document(documents, max_tokens=512):
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    chunks = []
    for document in documents:
        header = f"Canadian Aviation Regulations | {document['hierarchy']} | {document['section']}"
        header = header.strip(" |") + "\n"
        if token_count(header) >= max_tokens:
            raise ValueError(f"Heading exceeds token limit for {document['section']}")
        metadata = {key: value for key, value in document.items() if key not in {"id", "lines"}}
        current = ""
        part = 0

        def emit(text):
            nonlocal part
            part += 1
            chunks.append({
                "id": f"car-{document['id']}-{part}",
                "text": header + text,
                "metadata": {**metadata, "chunk_part": part},
            })

        for line, ancestors in document["lines"]:
            separator = "\n" if current else ""
            if token_count(header + current + separator + line) <= max_tokens:
                current += separator + line
                continue
            if current:
                emit(current)
                current = ""
            context = "\n".join(ancestors)
            prefix = header + (context + "\n" if context else "")
            if token_count(prefix) >= max_tokens:
                raise ValueError(f"Ancestor context exceeds token limit for {document['section']}")
            for piece in _split_line(line, prefix, max_tokens):
                if current:
                    emit(current)
                current = (context + "\n" if context else "") + piece
        if current:
            emit(current)
    if not chunks:
        raise ValueError("No CAR chunks produced")
    return chunks
