# spam_check.py
import re
from collections import Counter

# List of flagged words/phrases
FLAGGED_KEYWORDS = {
    "mayavada": [
        "i am krishna", "we are all god", "krishna is me",
        "all paths are same", "one consciousness"
    ],
    "troll": [
        "lol", "lmao", "cringe", "bhakt", "fake", "cope"
    ],
    "ads": [
        "buy followers", "free crypto", "click link", "whatsapp group",
        "telegram join", "xxx", "porn", "http://", "https://"
    ]
}

def check_spam(message: str) -> dict:
    """
    Evaluates a user's message for spam/troll/Mayavada content.
    Returns suspicion score and reasoning.
    """
    msg = message.lower()
    score = 0
    reasons = []

    # 1. Keyword checks
    for category, words in FLAGGED_KEYWORDS.items():
        for word in words:
            if word in msg:
                score += 3 if category == "mayavada" else 2
                reasons.append(f"Contains {category} phrase: '{word}'")

    # 2. All caps spam
    if msg.isupper() and len(msg) > 10:
        score += 2
        reasons.append("All caps shouting")

    # 3. Repeated characters/emojis
    if re.search(r"(.)\1{5,}", msg):
        score += 2
        reasons.append("Excessive repeated characters/emojis")

    # 4. Message length anomaly
    if len(msg) < 3:
        score += 1
        reasons.append("Very short/low-effort message")

    # Final classification
    if score >= 6:
        verdict = "SPAM"
    elif 3 <= score < 6:
        verdict = "SUSPICIOUS"
    else:
        verdict = "CLEAN"

    return {
        "score": score,
        "verdict": verdict,
        "reasons": reasons
    }

# Example test
if __name__ == "__main__":
    tests = [
        "I am Krishna, worship me",
        "LOL LOL LOL bhakt cringe",
        "Click this link for free crypto http://spam.com",
        "Hare Krishna 🙏",
    ]
    for t in tests:
        print(f"\nMessage: {t}")
        print(check_spam(t))
