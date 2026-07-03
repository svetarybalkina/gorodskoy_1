from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Category, Material, ProblemQuery, ResidentQuestion, Setting, Topic


TOPICS = [
    ("housing", "ЖКХ", True, 10),
    ("polyclinics", "Поликлиники", False, 20),
    ("education", "Образование", False, 30),
    ("social", "Социальные вопросы", False, 40),
    ("public_services", "Госуслуги", False, 50),
    ("transport", "Транспорт", False, 60),
    ("improvement", "Благоустройство", False, 70),
    ("animals", "Животные", False, 80),
    ("other_administrative", "Другие административные темы", False, 90),
]

BASE_CATEGORIES = {
    "housing": [
        ("heating", "Отопление", 10),
        ("water", "Вода", 20),
        ("entrance", "Подъезд", 30),
        ("yard", "Двор", 40),
        ("waste", "Мусор / отходы", 50),
        ("management_company", "Управляющая компания", 60),
        ("bills", "Квитанции / начисления", 70),
        ("animals", "Животные", 80),
        ("other", "Другое", 90),
    ],
    "animals": [],
}

DETAILED_ANIMAL_CATEGORY_SLUGS = {
    "stray_dogs",
    "animal_capture",
    "aggressive_animals",
    "shelters",
    "pet_rules",
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

    housing_animals_category = session.scalar(
        select(Category).where(
            Category.topic_id == topics_by_slug["housing"].id,
            Category.slug == "animals",
        )
    )
    detailed_animal_categories = list(
        session.scalars(
            select(Category).where(Category.slug.in_(DETAILED_ANIMAL_CATEGORY_SLUGS))
        )
    )
    detailed_animal_category_ids = [category.id for category in detailed_animal_categories]
    for category in detailed_animal_categories:
        category.is_public = False
        category.is_confirmed = True
    if housing_animals_category is not None and detailed_animal_category_ids:
        session.query(Material).filter(Material.category_id.in_(detailed_animal_category_ids)).update(
            {Material.category_id: housing_animals_category.id},
            synchronize_session=False,
        )
        session.query(ResidentQuestion).filter(
            ResidentQuestion.category_id.in_(detailed_animal_category_ids)
        ).update(
            {ResidentQuestion.category_id: housing_animals_category.id},
            synchronize_session=False,
        )
        session.query(ProblemQuery).filter(
            ProblemQuery.category_id.in_(detailed_animal_category_ids)
        ).update(
            {ProblemQuery.category_id: housing_animals_category.id},
            synchronize_session=False,
        )

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
