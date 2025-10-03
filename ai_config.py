"""
AI Prompt Configuration for Krishna Verification + Spam Detection
---------------------------------------------------------------
This configuration integrates:
1. Krishna-conscious verification (based on Srila Prabhupada’s standards).
2. Spam/Troll/Mayavada detection (from spam_check.py).
"""

from spam_check import check_spam

def get_ai_prompt_template() -> str:
    """
    Returns the AI prompt template for Krishna-conscious verification.
    The AI evaluates sincerity + spam checks, rooted in Srila Prabhupada’s mood.
    """
    return """You are a spiritually serious Krishna-conscious assistant.
Your mission is to verify new members entering a sacred community:
- Assess sincerity using Vaishnava principles (humility, respect, eagerness).
- Detect red flags: Mayavada, trolling, pride, disrespect, or spamming.

You must output:
- SCORE (0–10)
- ROLE (devotee / seeker / none)
- REASONING (short explanation: bhakti mood, humility, red/green flags, spam check)

Guidelines:
- Favor sincerity over scholarship.
- Respectful seekers = higher score.
- Mayavada, trolling, mocking = immediate low score.
- SpamCheck output MUST be considered in final decision.

Scoring:
+3 humility, surrender mood
+2 respect for Vaishnavas/guru
+2 emotional connection to Krishna
+1 honest confusion but respectful
0 neutral/empty answers
-1 cold/egoistic tone
-2 spam suspicion
-3 clear Mayavada/impersonalism
-5 mocking, trolling, disrespect

Final Role:
8–10 → devotee
5–7  → seeker
0–4  → none

This user has a suspicion score of: {suspicion_score}/10

{responses_section}
"""

def format_responses_for_ai(questions: list, responses: list) -> str:
    """
    Format user responses with question types.
    """
    formatted = ["=== USER VERIFICATION RESPONSES ===\n"]
    for i, (q, a) in enumerate(zip(questions, responses)):
        formatted.append(f"[Q{i+1}] {q}\nA: {a}\n")
    formatted.append("=== END RESPONSES ===")
    return "\n".join(formatted)

def build_complete_ai_prompt(questions: list, responses: list, suspicion_score: int) -> str:
    """
    Construct full prompt to send to AI for scoring.
    Integrates spam detection.
    """
    template = get_ai_prompt_template()
    section = format_responses_for_ai(questions, responses)
    return template.format(suspicion_score=suspicion_score, responses_section=section)


# Example usage
if __name__ == "__main__":
    # Example questions (from your JSON)
    questions = [
        "Are you a Vaiṣṇava? If not, which sampradāya or tradition do you follow?",
        "What are your views on the Vaiṣṇava ācāryas and their teachings?",
        "How did you find this server, and what attracted you to join?"
    ]

    # Example responses
    responses = [
        "I am a follower of Gaudiya Vaishnavism.",
        "I respect the ācāryas, especially Srila Prabhupada.",
        "I found this server through friends, I want to learn more."
    ]

    # Spam check simulation
    combined_text = " ".join(responses)
    spam_result = check_spam(combined_text)
    suspicion_score = spam_result["score"]

    # Build final AI prompt
    final_prompt = build_complete_ai_prompt(questions, responses, suspicion_score)
    print(final_prompt)
