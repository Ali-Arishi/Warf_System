import os

# id الموديل من صفحة Model access في Bedrock. يُفضّل ضبطه عبر متغير البيئة.
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

TEMPERATURE_SUMMARY = 0.3
TEMPERATURE_DECISIONS = 0.2
MAX_TOKENS_SUMMARY = 600
MAX_TOKENS_DECISIONS = 700
