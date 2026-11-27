# src/rtfm_agent/generation/prompt.py
"""
Builds the prompt from the retrieved chunks, and parses the model's reply.

The model sees each chunk as a numbered excerpt ([1], [2], ...) with its
page and URL, and is told to answer only from those excerpts and cite them
by number. The reply format is fixed (explanation, one python block, one
json IAM policy block) so it can be parsed and checked automatically.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# The exact sentence the model must use when the excerpts don't answer the
# question. Detected by parse_answer, so keep the two in sync.
NOT_FOUND = "I couldn't find this in the Boto3 docs I have."

SYSTEM_PROMPT = f"""You are RTFM, an assistant for the AWS SDK for Python (Boto3).
You answer using ONLY the numbered documentation excerpts in the user message.

Rules:
1. Only use Boto3 methods, parameters and IAM actions that appear in, or follow directly from, the excerpts. Never guess an API name.
2. If the excerpts do not contain the answer, reply with exactly this sentence and nothing else:
{NOT_FOUND}
3. Cite the excerpts you used, inline, by number, like [1] or [2][3].
4. Reply in this format:
   - A short explanation (2 to 4 sentences) with citations.
   - One ```python code block: a complete, runnable example using boto3.client(...). Use placeholder names such as 'my-bucket'. Never put credentials in code.
   - One ```json code block: a least-privilege IAM policy allowing only the actions the code calls. Prefer specific resource ARNs with placeholders over "*".
"""


def format_sources(chunks: Sequence) -> str:
    """Numbered excerpts. Each chunk's text already starts with its
    breadcrumb (e.g. "s3 > client > put_object")."""
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        url = chunk.metadata.get("source_url", "")
        parts.append(f"[{i}] {url}\n{chunk.text.strip()}")
    return "\n\n---\n\n".join(parts)


def build_prompt(question: str, chunks: Sequence) -> Tuple[str, str]:
    """Return (system, user) messages."""
    user = (
        f"Documentation excerpts:\n\n{format_sources(chunks)}\n\n"
        f"===\n\nQuestion: {question.strip()}"
    )
    return SYSTEM_PROMPT, user


_PYTHON_BLOCK = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.S | re.I)
_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.S | re.I)
_ANY_BLOCK = re.compile(r"```.*?```", re.S)
_CITATION = re.compile(r"\[(\d+)\]")


@dataclass
class ParsedAnswer:
    text: str
    found: bool
    code: Optional[str] = None
    policy: Optional[Dict] = None
    cited: List[int] = field(default_factory=list)       # valid source numbers, in order
    warnings: List[str] = field(default_factory=list)


def parse_answer(raw: str, n_sources: int) -> ParsedAnswer:
    text = (raw or "").strip()
    if not text or text.startswith(NOT_FOUND):
        return ParsedAnswer(text=NOT_FOUND, found=False)

    out = ParsedAnswer(text=text, found=True)

    code = _PYTHON_BLOCK.search(text)
    if code:
        out.code = code.group(1).strip()
    else:
        out.warnings.append("No Python code block in the answer.")

    policy = _JSON_BLOCK.search(text)
    if policy:
        try:
            data = json.loads(policy.group(1))
        except json.JSONDecodeError as e:
            out.warnings.append(f"IAM policy is not valid JSON: {e.msg}.")
        else:
            # Small models sometimes put a sample API response here instead
            if isinstance(data, dict) and "Statement" in data:
                out.policy = data
            else:
                out.warnings.append("The JSON block is not an IAM policy (no Statement).")
    else:
        out.warnings.append("No IAM policy in the answer.")

    prose = _ANY_BLOCK.sub("", text)  # so items[0] in code isn't read as a citation
    numbers = list(dict.fromkeys(int(n) for n in _CITATION.findall(prose)))
    out.cited = [n for n in numbers if 1 <= n <= n_sources]
    bad = [n for n in numbers if not 1 <= n <= n_sources]
    if bad:
        out.warnings.append(f"Cites sources that don't exist: {bad} (only {n_sources} given).")
    if not out.cited:
        out.warnings.append("The answer doesn't cite any source.")
    return out
