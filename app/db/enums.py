from enum import StrEnum


class SourceKind(StrEnum):
    OFFICIAL_BOT = "official_bot"
    OFFICIAL_CHANNEL = "official_channel"
    WEBSITE = "website"
    TELEGRAM_BOT = "telegram_bot"


class MaterialType(StrEnum):
    OFFICIAL_ANSWER = "official_answer"
    OFFICIAL_POST = "official_post"


class MaterialStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    ARCHIVED = "archived"
    HIDDEN = "hidden"
    DUPLICATE = "duplicate"
    PENDING_DELETE = "pending_delete"


class RecommendationType(StrEnum):
    CONTACT = "contact"
    CONDITION = "condition"
    DEADLINE = "deadline"
    RESTRICTION = "restriction"
    NEXT_STEP = "next_step"


class ImportStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED_PUBLIC = "approved_public"
    REDACTED = "redacted"
    HIDE_MATERIAL = "hide_material"


class LinkReason(StrEnum):
    IMPORTED_PAIR = "imported_pair"
    ADMIN_CONFIRMED = "admin_confirmed"
    SIMILAR = "similar"


class ProblemQueryChannel(StrEnum):
    WEBSITE = "website"
    TELEGRAM_BOT = "telegram_bot"


class ProblemQueryAction(StrEnum):
    REPHRASE = "rephrase"
    CHOOSE_CATEGORY = "choose_category"
    VIEW_SIMILAR = "view_similar"
    NO_ACTION = "no_action"


class DictionaryCandidateType(StrEnum):
    MARKER = "marker"
    SYNONYM = "synonym"
    QUESTION_VARIANT = "question_variant"
    CATEGORY = "category"


class DictionaryCandidateSource(StrEnum):
    SEARCH = "search"
    IMPORT = "import"


class DictionaryCandidateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
