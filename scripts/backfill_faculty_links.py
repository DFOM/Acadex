from app import app
from models import db, Course, DatasetRegistry
from utils import sync_course_faculty


def main() -> None:
    with app.app_context():
        updated_courses = 0
        missing_faculty = 0
        dataset_count = 0

        dataset_rows = DatasetRegistry.query.order_by(DatasetRegistry.created_at.asc(), DatasetRegistry.id.asc()).all()
        for dataset in dataset_rows:
            dataset_count += 1
            dataset_name = str(getattr(dataset, 'name', '') or '').strip()
            source = str(getattr(dataset, 'source', '') or '').strip().lower()
            if not dataset_name:
                continue

            query = Course.query.filter(db.func.lower(db.func.coalesce(Course.dataset_name, '')) == dataset_name.lower())
            if source:
                query = query.filter(db.func.lower(db.func.coalesce(Course.source, '')) == source)

            for course in query.order_by(Course.id.asc()).all():
                before = len(course.faculty or [])
                names = sync_course_faculty(course, course.instructor)
                after = len(course.faculty or [])
                if after != before:
                    updated_courses += 1
                if not names:
                    missing_faculty += 1
                    print(f"WARN: Missing faculty for dataset={dataset_name} course_id={course.id} course={course.course_number}")
                else:
                    course.instructor = '; '.join(names)

        # Fallback pass for rows not tied to registry entries.
        known_ids = {
            c.id
            for c in Course.query.filter(Course.dataset_name.isnot(None)).all()
        }
        for course in Course.query.order_by(Course.id.asc()).all():
            if course.id in known_ids:
                continue
            before = len(course.faculty or [])
            names = sync_course_faculty(course, course.instructor)
            after = len(course.faculty or [])
            if after != before:
                updated_courses += 1
            if not names:
                missing_faculty += 1
                print(f"WARN: Missing faculty for unregistered course_id={course.id} course={course.course_number}")
            else:
                course.instructor = '; '.join(names)

        db.session.commit()
        print(
            "Backfill complete. "
            f"Datasets processed: {dataset_count}, "
            f"courses updated: {updated_courses}, "
            f"courses missing faculty: {missing_faculty}"
        )


if __name__ == '__main__':
    main()
