"""Localisation for user-facing `detail` strings.

The client sends a numeric `langCode` header (1 = Arabic, 2 = English) set by
`LanguageInterceptor`, and it does **not** localise `badResponse` messages —
whatever the server puts in `detail` is shown verbatim. So every message that
can reach a user lives here in both languages.
"""

from __future__ import annotations

from enum import Enum

from starlette.requests import Request


class Lang(str, Enum):
    AR = "ar"
    EN = "en"


def lang_from_request(request: Request) -> Lang:
    """`langCode: 1` -> Arabic. Anything else (incl. missing) -> English."""
    raw = request.headers.get("langCode") or request.headers.get("langcode")
    return Lang.AR if raw == "1" else Lang.EN


_MESSAGES: dict[str, dict[Lang, str]] = {
    "error.generic": {
        Lang.EN: "Something went wrong. Please try again.",
        Lang.AR: "حدث خطأ ما. برجاء المحاولة مرة أخرى.",
    },
    "error.server": {
        Lang.EN: "We hit an unexpected problem. Please try again shortly.",
        Lang.AR: "واجهتنا مشكلة غير متوقعة. برجاء المحاولة بعد قليل.",
    },
    "error.rate_limited": {
        Lang.EN: "Too many attempts. Please wait a while and try again.",
        Lang.AR: "محاولات كثيرة جدًا. برجاء الانتظار قليلًا ثم المحاولة مرة أخرى.",
    },
    "error.validation": {
        Lang.EN: "Some of the information you entered isn't valid.",
        Lang.AR: "بعض البيانات التي أدخلتها غير صالحة.",
    },
    # --- auth ---
    "auth.email_taken": {
        Lang.EN: "That email is already registered.",
        Lang.AR: "هذا البريد الإلكتروني مسجل بالفعل.",
    },
    "auth.invalid_credentials": {
        Lang.EN: "Incorrect email or password.",
        Lang.AR: "البريد الإلكتروني أو كلمة المرور غير صحيحة.",
    },
    "auth.weak_password": {
        Lang.EN: "Password must be at least 8 characters and include a number and a symbol.",
        Lang.AR: "يجب أن تتكون كلمة المرور من 8 أحرف على الأقل وتشمل رقمًا ورمزًا.",
    },
    "auth.invalid_token": {
        Lang.EN: "Your session has expired. Please sign in again.",
        Lang.AR: "انتهت جلستك. برجاء تسجيل الدخول مرة أخرى.",
    },
    "auth.otp_invalid": {
        Lang.EN: "That code isn't correct. Please check and try again.",
        Lang.AR: "هذا الرمز غير صحيح. برجاء التحقق والمحاولة مرة أخرى.",
    },
    "auth.otp_expired": {
        Lang.EN: "That code has expired. Request a new one.",
        Lang.AR: "انتهت صلاحية هذا الرمز. اطلب رمزًا جديدًا.",
    },
    "auth.otp_cooldown": {
        Lang.EN: "Please wait a moment before requesting another code.",
        Lang.AR: "برجاء الانتظار قليلًا قبل طلب رمز آخر.",
    },
    "auth.not_verified": {
        Lang.EN: "Please verify your email address first.",
        Lang.AR: "برجاء تأكيد بريدك الإلكتروني أولًا.",
    },
    "auth.email_failed": {
        Lang.EN: "We couldn't send that email. Please try again shortly.",
        Lang.AR: "لم نتمكن من إرسال البريد الإلكتروني. برجاء المحاولة بعد قليل.",
    },
    "auth.social_invalid": {
        Lang.EN: "We couldn't verify your sign-in. Please try again.",
        Lang.AR: "لم نتمكن من التحقق من تسجيل دخولك. برجاء المحاولة مرة أخرى.",
    },
    "auth.social_unavailable": {
        Lang.EN: "Sign-in with this provider isn't available right now. Please try again shortly.",
        Lang.AR: "تسجيل الدخول عبر هذا المزود غير متاح حاليًا. برجاء المحاولة بعد قليل.",
    },
    "auth.reset_invalid": {
        Lang.EN: "This reset link is no longer valid. Request a new one.",
        Lang.AR: "رابط إعادة التعيين لم يعد صالحًا. اطلب رابطًا جديدًا.",
    },
    # --- generic resource ---
    "resource.not_found": {
        Lang.EN: "We couldn't find what you were looking for.",
        Lang.AR: "لم نتمكن من العثور على ما تبحث عنه.",
    },
    "resource.forbidden": {
        # Deliberately NOT a 403 at the HTTP layer — see AuthorizationInterceptor.
        Lang.EN: "You don't have access to that.",
        Lang.AR: "ليس لديك صلاحية للوصول إلى ذلك.",
    },
    # --- food ---
    "food.no_image": {
        Lang.EN: "Please attach a photo of your meal.",
        Lang.AR: "برجاء إرفاق صورة لوجبتك.",
    },
    "food.bad_image": {
        Lang.EN: "We couldn't read that image. Try taking the photo again.",
        Lang.AR: "لم نتمكن من قراءة هذه الصورة. حاول التقاطها مرة أخرى.",
    },
    "food.image_too_large": {
        Lang.EN: "That photo is too large. Please try a smaller one.",
        Lang.AR: "هذه الصورة كبيرة جدًا. برجاء تجربة صورة أصغر.",
    },
    "food.analysis_failed": {
        Lang.EN: "We couldn't analyse that meal. Please try scanning again.",
        Lang.AR: "لم نتمكن من تحليل هذه الوجبة. برجاء إعادة المسح.",
    },
    "food.not_food": {
        Lang.EN: "We couldn't find any food in that photo. Try scanning again.",
        Lang.AR: "لم نتمكن من العثور على طعام في هذه الصورة. حاول المسح مرة أخرى.",
    },
    "food.no_description": {
        Lang.EN: "Please describe your meal.",
        Lang.AR: "برجاء وصف وجبتك.",
    },
    "food.scan_limit": {
        Lang.EN: "You've reached today's scan limit. It resets at midnight.",
        Lang.AR: "لقد وصلت إلى الحد اليومي للمسح. يُعاد التعيين عند منتصف الليل.",
    },
    "food.barcode_unknown": {
        Lang.EN: "We don't know that product yet. Try scanning the meal instead.",
        Lang.AR: "لا نعرف هذا المنتج بعد. جرّب مسح الوجبة بدلًا من ذلك.",
    },
    # --- profile ---
    "profile.plan_missing": {
        Lang.EN: "Finish your onboarding to get your daily plan.",
        Lang.AR: "أكمل خطوات التسجيل للحصول على خطتك اليومية.",
    },
    "profile.no_photo": {
        Lang.EN: "Please attach a photo.",
        Lang.AR: "برجاء إرفاق صورة.",
    },
    "profile.bad_photo": {
        Lang.EN: "We couldn't read that image. Try choosing another photo.",
        Lang.AR: "لم نتمكن من قراءة هذه الصورة. حاول اختيار صورة أخرى.",
    },
    "profile.photo_too_large": {
        Lang.EN: "That photo is too large. Please try a smaller one.",
        Lang.AR: "هذه الصورة كبيرة جدًا. برجاء تجربة صورة أصغر.",
    },
}


def t(key: str, lang: Lang, **fmt: object) -> str:
    entry = _MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry[Lang.EN]
    return text.format(**fmt) if fmt else text


__all__ = ["Lang", "lang_from_request", "t"]
