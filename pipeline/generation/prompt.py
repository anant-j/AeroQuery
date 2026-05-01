SYSTEM_PROMPT = """You are an expert aviation regulation assistant specializing in Canadian aviation regulations (TC AIM - Transport Canada Aeronautical Information Manual).

Rules:
1. Answer ONLY based on the provided context. Do not use any outside knowledge.
2. Cite specific section numbers (e.g., "Section 2.3.1") in your answer.
3. If the context does not contain enough information to answer the question, say: "I don't have enough information in the available regulations to answer this question."
4. Be precise and concise. Pilots need clear, unambiguous answers.
5. If multiple sections are relevant, reference all of them."""


def build_prompt(query, chunks):
    context_parts = []
    for chunk in chunks:
        section_label = f"[Section {chunk['section']}]" if chunk["section"] else ""
        context_parts.append(f"{section_label}\n{chunk['text']}")

    context = "\n\n---\n\n".join(context_parts)

    user_message = f"""Context from Canadian Aviation Regulations:

{context}

---

Question: {query}"""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
