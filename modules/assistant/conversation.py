"""Complete sentence boundary for single-packet conversational replies."""
import re


def complete_reply(text, budget):
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'(?:…|\.{3,})\s*$', '', text).strip()
    # Never send a model fragment or a truncation marker.
    sentences = re.findall(r'.*?[.!?](?:[»”\"])?(?=\s|$)', text)
    kept = []
    for sentence in sentences:
        candidate = ' '.join(kept+[sentence.strip()])
        if len(candidate.encode('utf-8')) > budget:
            break
        kept.append(sentence.strip())
    return ' '.join(kept) or 'Je n’ai pas pu formuler une réponse assez courte.'
