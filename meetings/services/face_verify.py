# -*- coding: utf-8 -*-
"""
face_verify.py — مطابقة صورة مرجعية مع سيلفي

deepface يُستورد داخل الدالة (lazy) وليس على مستوى الموديول،
لأنه يسحب TensorFlow (~1.8GB) ويتجاوز حد Vercel (500MB).
"""


def verify_face(reference_img_path: str, selfie_img_path: str):
    """
    ترجع (is_match: bool, confidence: float) — نفس التوقيع السابق.

    عند عدم توفر المكتبة أو فشل الكشف ترجع (False, 0.0) — fail closed.
    """
    try:
        from deepface import DeepFace
    except ImportError:
        return False, 0.0

    try:
        result = DeepFace.verify(
            img1_path=reference_img_path,
            img2_path=selfie_img_path,
            enforce_detection=True,
            detector_backend="opencv",
            model_name="VGG-Face",
            distance_metric="cosine",
        )
    except (ValueError, IndexError):
        # لا يوجد وجه واضح في إحدى الصورتين
        return False, 0.0

    is_match = bool(result.get("verified", False))
    distance = float(result.get("distance", 999.0))
    confidence = max(0.0, 1.0 - distance)
    return is_match, confidence
