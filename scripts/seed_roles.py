#!/usr/bin/env python3
"""
Script to seed roles in the Just-EdTech database.
This script creates the three main roles: super_admin, tenant_admin, tenant_user
"""

import asyncio
import sys

from sqlalchemy import text

from app.db.connector import get_session
from app.models.roles import Role


async def seed_roles():
    """Seed the roles table with default roles"""
    print("🚀 Just-EdTech Role Seeder")
    print("=" * 40)

    # Define the three main roles
    roles_data = [
        {
            "name": "super_admin",
            "description": "Super administrator with full system access across all tenants",
        },
        {
            "name": "tenant_admin",
            "description": "Tenant administrator with full access within their tenant",
        },
        {
            "name": "tenant_user",
            "description": "Regular tenant user with limited access within their tenant",
        },
    ]

    async for session in get_session():
        try:
            print("📋 Seeding roles...")

            for role_data in roles_data:
                # Check if role already exists
                existing_role = await session.execute(
                    text("SELECT id FROM roles WHERE name = :name"),
                    {"name": role_data["name"]},
                )

                if existing_role.scalar():
                    print(
                        f"   ⏭️  Role '{role_data['name']}' already exists, skipping..."
                    )
                    continue

                # Create new role
                role = Role(
                    name=role_data["name"],
                )

                session.add(role)
                print(f"   ✅ Created role: {role_data['name']}")

            await session.commit()
            print("\n🎉 Role seeding completed successfully!")

        except Exception as e:
            print(f"❌ Error seeding roles: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
        return


async def create_default_tenant_and_admin():
    """Create default tenant and super admin user"""
    from app.models.tenants import Tenant
    from app.models.users import User
    from app.utils.auth import get_password_hash

    print("\n🔧 Creating default tenant and super admin...")

    async for session in get_session():
        try:
            # Create default tenant if not exists
            existing_tenant = await session.execute(
                text("SELECT id FROM tenants WHERE domain = :domain"),
                {"domain": "system.local"},
            )

            tenant_id = existing_tenant.scalar()
            if not tenant_id:
                tenant = Tenant(name="System Tenant", domain="system.local")
                session.add(tenant)
                await session.flush()
                tenant_id = tenant.id
                print("   ✅ Created system tenant")
            else:
                print("   ⏭️  System tenant already exists")

            # Get super_admin role
            super_admin_role = await session.execute(
                text("SELECT id FROM roles WHERE name = :name"), {"name": "super_admin"}
            )
            role_id = super_admin_role.scalar()

            if not role_id:
                print("   ❌ super_admin role not found! Run role seeder first.")
                return

            # Create super admin user if not exists
            existing_admin = await session.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": "superadmin@justedtech.com"},
            )

            if not existing_admin.scalar():
                admin_user = User(
                    tenant_id=tenant_id,
                    name="Super Administrator",
                    email="superadmin@justedtech.com",
                    password_hash=get_password_hash("SuperAdmin123!"),
                    role_id=role_id,
                )
                session.add(admin_user)
                print("   ✅ Created super admin user")
                print("      Email: superadmin@justedtech.com")
                print("      Password: SuperAdmin123!")
            else:
                print("   ⏭️  Super admin user already exists")

            await session.commit()
            print("\n🎯 Default setup completed!")

        except Exception as e:
            print(f"❌ Error creating defaults: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
        return


async def main():
    """Main function"""
    try:
        # Seed roles first
        await seed_roles()

        # Create default tenant and admin
        if len(sys.argv) > 1 and sys.argv[1] == "--with-defaults":
            await create_default_tenant_and_admin()

    except Exception as e:
        print(f"\n💥 Seeding failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
