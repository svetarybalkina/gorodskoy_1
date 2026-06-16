from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Category, Setting, Topic


TOPICS = [
    ("housing", "ЖКХ", True, 10),
    ("polyclinics", "Поликлиники", False, 20),
    ("education", "Образование", False, 30),
    ("social", "Социальные вопросы", False, 40),
    ("public_services", "Госуслуги", False, 50),
    ("transport", "Транспорт", False, 60),
    ("improvement", "Благоустройство", False, 70),
    ("animals", "Животные", True, 80),
    ("other_administrative", "Другие административные темы", False, 90),
]

BASE_CATEGORIES = {
    "housing": [
        ("heating", "Отопление", 10),
        ("water", "Вода", 20),
        ("entrance", "Подъезд", 30),
        ("yard", "Двор", 40),
        ("management_company", "Управляющая компания", 50),
        ("bills", "Квитанции / начисления", 60),
        ("other", "Другое", 100),
    ],
    "animals": [
        ("stray_dogs", "Безнадзорные собаки", 10),
        ("animal_capture", "Отлов безнадзорных животных", 20),
        ("aggressive_animals", "Агрессивные животные", 30),
        ("shelters", "Приюты и передержка", 40),
        ("pet_rules", "Правила содержания животных", 50),
        ("other", "Другое", 100),
    ],
}


def seed_initial_data(session: Session) -> None:
    topics_by_slug: dict[str, Topic] = {}
    for slug, name, is_public, sort_order in TOPICS:
        topic = session.scalar(select(Topic).where(Topic.slug == slug))
        if topic is None:
            topic = Topic(slug=slug, name=name, is_public=is_public, sort_order=sort_order)
            session.add(topic)
            session.flush()
        else:
            topic.name = name
            topic.is_public = is_public
            topic.sort_order = sort_order
        topics_by_slug[slug] = topic

    for topic_slug, categories in BASE_CATEGORIES.items():
        topic = topics_by_slug[topic_slug]
        for slug, name, sort_order in categories:
            category = session.scalar(
                select(Category).where(Category.topic_id == topic.id, Category.slug == slug)
            )
            if category is None:
                session.add(
                    Category(
                        topic_id=topic.id,
                        slug=slug,
                        name=name,
                        is_public=True,
                        is_confirmed=True,
                        sort_order=sort_order,
                    )
                )
            else:
                category.name = name
                category.is_public = True
                category.is_confirmed = True
                category.sort_order = sort_order

    setting = session.scalar(select(Setting).where(Setting.key == "ADS_ENABLED"))
    if setting is None:
        session.add(
            Setting(
                key="ADS_ENABLED",
                value="false",
                description="Technical flag for future ads support. Ads are disabled in MVP.",
            )
        )
    else:
        setting.value = "false"

    session.commit()
