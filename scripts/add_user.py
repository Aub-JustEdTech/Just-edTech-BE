#!/usr/bin/env python3
"""
Simple script to add one user to the Just-EdTech database.
Usage: python scripts/add_user.py
"""

import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.crud.users import user  # noqa: E402
from app.db.connector import SessionLocal, engine  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.schemas.users import UserCreate  # noqa: E402


def main():
    """Add a test user to the database"""

    # Create tables if they don't exist
    print("📋 Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables ready!")

    # Create database session
    db = SessionLocal()

    try:
        # Define user data
        email = "testuser@justedtech.com"
        username = "testuser"
        password = "TestPassword123!"
        full_name = "Test User"

        print(f"\n👤 Creating user: {username} ({email})")

        # Check if user already exists
        existing_user = user.get_by_email(db, email=email)
        if existing_user:
            print(f"❌ User with email '{email}' already exists!")
            print(f"   User ID: {existing_user.id}")
            print(f"   Username: {existing_user.username}")
            return

        existing_username = user.get_by_username(db, username=username)
        if existing_username:
            print(f"❌ User with username '{username}' already exists!")
            print(f"   User ID: {existing_username.id}")
            print(f"   Email: {existing_username.email}")
            return

        # Create user
        user_create = UserCreate(
            email=email,
            username=username,
            password=password,
            full_name=full_name,
            is_active=True,
        )

        db_user = user.create(db, user_create=user_create)

        print("✅ User created successfully!")
        print(f"   ID: {db_user.id}")
        print(f"   Email: {db_user.email}")
        print(f"   Username: {db_user.username}")
        print(f"   Full Name: {db_user.full_name}")
        print(f"   Is Active: {db_user.is_active}")
        print(f"   Is Superuser: {db_user.is_superuser}")

        print("\n🔑 Login credentials:")
        print(f"   Username: {username}")
        print(f"   Password: {password}")

    except Exception as e:
        print(f"❌ Error creating user: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
