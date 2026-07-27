"""DSPY signature for grounded doc Q&A."""

import dspy


class AnswerFromDocs(dspy.Signature):
    """Answer the question using ONLY the provided documentation context.

    If the answer is not contained in the context, say that the documentation
    does not cover it rather than guessing. Be concise and cite concrete
    details from the context where possible.
    """

    question: str = dspy.InputField(desc="The user's natural-language question.")
    context: str = dspy.InputField(desc="Relevant documentation excerpts.")
    answer: str = dspy.OutputField(desc="A grounded answer derived from the context.")
