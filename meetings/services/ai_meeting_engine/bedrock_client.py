# -*- coding: utf-8 -*-
"""
bedrock_client.py — عميل موحّد لـ Amazon Bedrock

يستخدم Converse API: واجهة واحدة تشتغل مع أي موديل في Bedrock
(Claude, Llama, Mistral...) بنفس الشكل، بدل اختلاف JSON schema
بين موديل وآخر في invoke_model.

الإعداد عبر متغيرات البيئة (.env أو Vercel/Railway Variables):
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_REGION            (افتراضي us-east-1)
    BEDROCK_MODEL_ID      (id الموديل من صفحة Model access في Bedrock)
"""

import os
import boto3

_client = None


def _get_client():
    """إنشاء الـ client مرة واحدة (lazy) وإعادة استخدامه — إنشاؤه في كل نداء بطيء."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
    return _client


def chat(system_prompt: str, user_text: str,
         temperature: float = 0.3, max_tokens: int = 600) -> str:
    """
    نداء واحد للموديل. يرجّع نص الرد.

    يقابل client.chat.completions.create في OpenAI، لكن عبر Bedrock.
    """
    model_id = os.getenv("BEDROCK_MODEL_ID", "")
    if not model_id:
        raise RuntimeError(
            "BEDROCK_MODEL_ID غير مضبوط. حطّه في متغيرات البيئة "
            "(id الموديل من صفحة Model access في Bedrock)."
        )

    resp = _get_client().converse(
        modelId=model_id,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return resp["output"]["message"]["content"][0]["text"]
