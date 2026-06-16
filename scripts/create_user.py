#!/usr/bin/env python3
"""
Script to create a user in the Just-EdTech database.
This script can be used for initial setup or testing purposes.
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


def create_tables():
    """Create database tables if they don't exist"""
    Base.metadata.create_all(bind=engine)


def create_user_in_db(
    email: str,
    username: str,
    password: str,
    full_name: str = None,
    is_superuser: bool = False,
):
    """Create a user in the database"""
    db = SessionLocal()
    try:
        # Check if user already exists
        existing_user = user.get_by_email(db, email=email)
        if existing_user:
            print(f"❌ User with email '{email}' already exists!")
            return None

        existing_username = user.get_by_username(db, username=username)
        if existing_username:
            print(f"❌ User with username '{username}' already exists!")
            return None

        # Create user
        user_create = UserCreate(
            email=email,
            username=username,
            password=password,
            full_name=full_name,
            is_active=True,
        )

        db_user = user.create(db, user_create=user_create)

        # Set superuser status if requested
        if is_superuser:
            db_user.is_superuser = True
            db.commit()
            db.refresh(db_user)

        print("✅ User created successfully!")
        print(f"   ID: {db_user.id}")
        print(f"   Email: {db_user.email}")
        print(f"   Username: {db_user.username}")
        print(f"   Full Name: {db_user.full_name}")
        print(f"   Is Active: {db_user.is_active}")
        print(f"   Is Superuser: {db_user.is_superuser}")

        return db_user

    except Exception as e:
        print(f"❌ Error creating user: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def main():
    """Main function with interactive user creation"""
    print("🚀 Just-EdTech User Creation Script")
    print("=" * 40)

    # Create tables if they don't exist
    print("📋 Creating database tables...")
    create_tables()
    print("✅ Database tables ready!")
    print()

    # Get user input
    print("👤 Enter user details:")
    email = input("Email: ").strip()
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    full_name = input("Full Name (optional): ").strip() or None

    is_superuser_input = input("Is Superuser? (y/N): ").strip().lower()
    is_superuser = is_superuser_input in ["y", "yes", "1", "true"]

    print()
    print("📝 Creating user...")

    # Validate inputs
    if not email or not username or not password:
        print("❌ Email, username, and password are required!")
        return

    # Create user
    result = create_user_in_db(
        email=email,
        username=username,
        password=password,
        full_name=full_name,
        is_superuser=is_superuser,
    )

    if result:
        print("\n🎉 User creation completed!")
    else:
        print("\n💥 User creation failed!")


def create_default_admin():
    """Create a default admin user for testing"""
    print("🔧 Creating default admin user...")

    # Create tables if they don't exist
    create_tables()

    result = create_user_in_db(
        email="admin@justedtech.com",
        username="admin",
        password="AdminPassword123!",
        full_name="Just-EdTech Administrator",
        is_superuser=True,
    )

    if result:
        print("\n🎯 Default admin user created!")
        print("   Login credentials:")
        print("   Username: admin")
        print("   Password: AdminPassword123!")
    else:
        print("\n💥 Failed to create default admin user!")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--admin":
        create_default_admin()
    else:
        main()
