# -*- coding: utf-8 -*-
"""
face.py — التعرف على الوجوه

مهم: deepface لا يُستورد على مستوى الموديول.
هو يسحب TensorFlow (~1.8GB) ويتجاوز حد Vercel (500MB).
الاستيراد يتم داخل الدالة (lazy)، فلو كانت المكتبة غير مثبّتة
يقلع التطبيق طبيعي وتتعطل ميزة الوجوه فقط.
"""

import math
import pickle
from pathlib import Path

THRESHOLD = 0.68
BASE_DIR = Path(__file__).resolve().parent
_EMBEDDINGS_PATH = BASE_DIR / "face_embeddings.pkl"

# كاش داخلي — لا تقرأه مباشرة، استخدم get_face_db()
_face_db_cache = None


def get_face_db() -> dict:
    """
    تحميل قاعدة الوجوه عند أول استخدام فقط (lazy).
    الملف السابق كان يُقرأ وقت الاستيراد، وهذا يكسر الإقلاع
    لو كان الملف مفقوداً أو نظام الملفات للقراءة فقط.
    """
    global _face_db_cache
    if _face_db_cache is None:
        try:
            with open(_EMBEDDINGS_PATH, "rb") as f:
                _face_db_cache = pickle.load(f)
        except (FileNotFoundError, pickle.UnpicklingError, EOFError):
            _face_db_cache = {}
    return _face_db_cache


# توافق مع الكود القديم: accounts/views.py و meetings/views.py
# يستوردان FACE_DB مباشرة. هذا يبقيهما شغّالين بدون تعديل.
class _LazyFaceDB(dict):
    def __getitem__(self, key):
        return get_face_db()[key]

    def get(self, key, default=None):
        return get_face_db().get(key, default)

    def __contains__(self, key):
        return key in get_face_db()

    def __iter__(self):
        return iter(get_face_db())

    def __len__(self):
        return len(get_face_db())

    def keys(self):
        return get_face_db().keys()

    def items(self):
        return get_face_db().items()

    def values(self):
        return get_face_db().values()


FACE_DB = _LazyFaceDB()


def face_recognition_available() -> bool:
    """هل مكتبة deepface مثبّتة؟ استخدمها في الـ views لإخفاء الميزة."""
    try:
        import deepface  # noqa: F401
        return True
    except ImportError:
        return False


def _cosine_similarity(a, b) -> float:
    """
    تشابه جيب التمام — بدون scipy.
    الكود السابق استورد scipy.spatial.distance.cosine، وscipy كان
    يجي تبعاً لـ deepface. إزالته توفّر ~90MB إضافية.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def verify_face(image_path: str, authorized_users: list) -> dict:
    """
    image_path: مسار الصورة
    authorized_users: قائمة أسماء/معرّفات المستخدمين المصرّح لهم

    ترجع dict فيه approved / user / confidence — نفس البنية السابقة،
    مع إضافة مفتاح 'error' عند الفشل.

    مهم أمنياً: عند أي فشل ترجع approved=False (fail closed).
    لا تجعلها ترجع True أبداً عند الخطأ — هذي بوابة تحقق.
    """
    denied = {"approved": False, "user": None, "confidence": 0.0}

    try:
        from deepface import DeepFace
    except ImportError:
        return {**denied, "error": "unavailable",
                "message": "خدمة التعرف على الوجوه غير مفعّلة على هذا الخادم."}

    face_db = get_face_db()
    if not face_db:
        return {**denied, "error": "no_embeddings",
                "message": "قاعدة بيانات الوجوه فارغة أو غير موجودة."}

    try:
        embedding = DeepFace.represent(
            img_path=image_path,
            model_name="ArcFace",
            enforce_detection=True,
        )[0]["embedding"]
    except (ValueError, IndexError):
        # enforce_detection=True يرمي ValueError لو ما فيه وجه واضح
        return {**denied, "error": "no_face_detected",
                "message": "لم يتم العثور على وجه واضح في الصورة."}

    best_user, best_score = None, 0.0
    for user in authorized_users:
        stored = face_db.get(user)
        if stored is None:
            continue
        score = _cosine_similarity(embedding, stored)
        if score > best_score:
            best_user, best_score = user, score

    # نأخذ أعلى تطابق بدل أول تطابق يتجاوز الحد — أدق عند تشابه الوجوه
    if best_score >= THRESHOLD:
        return {"approved": True, "user": best_user,
                "confidence": round(best_score, 2)}

    return {**denied, "confidence": round(best_score, 2)}
