#!/usr/bin/env python3
"""
Production Database Seeding Script for Just-EdTech

This script seeds the database with initial data for:
- roles
- tenants
- llm_models
- users
- api_keys
- chat_consumers
- chatbot_configs

Usage:
    python seed_database.py

Note: This script is designed for both local testing and production deployment.
All data is entered interactively to ensure no dummy data is hardcoded.
"""

import asyncio
import os
import sys
from datetime import datetime
from decimal import Decimal
from getpass import getpass
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.config import Settings
from app.models.api_keys import ApiKey
from app.models.chat_consumers import ChatConsumer
from app.models.chatbot_configs import ChatbotConfig
from app.models.llm_models import LLMModel
from app.models.roles import Role
from app.models.tenants import Tenant
from app.models.users import User
from app.utils.auth import get_password_hash


def print_header(text: str):
    """Print a formatted header"""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def get_input(prompt: str, required: bool = True, default: Optional[str] = None) -> Optional[str]:
    """Get user input with optional default value"""
    if default:
        prompt = f"{prompt} [{default}]"
    
    while True:
        value = input(f"{prompt}: ").strip()
        
        if not value and default:
            return default
        
        if not value and required:
            print("❌ This field is required. Please enter a value.")
            continue
            
        return value if value else None


def get_int_input(prompt: str, required: bool = True, default: Optional[int] = None) -> Optional[int]:
    """Get integer input"""
    if default is not None:
        prompt = f"{prompt} [{default}]"
    
    while True:
        value = input(f"{prompt}: ").strip()
        
        if not value and default is not None:
            return default
        
        if not value and not required:
            return None
        
        if not value and required:
            print("❌ This field is required. Please enter a value.")
            continue
        
        try:
            return int(value)
        except ValueError:
            print("❌ Please enter a valid integer.")


def get_float_input(prompt: str, required: bool = True, default: Optional[float] = None) -> Optional[float]:
    """Get float input"""
    if default is not None:
        prompt = f"{prompt} [{default}]"
    
    while True:
        value = input(f"{prompt}: ").strip()
        
        if not value and default is not None:
            return default
        
        if not value and not required:
            return None
        
        if not value and required:
            print("❌ This field is required. Please enter a value.")
            continue
        
        try:
            return float(value)
        except ValueError:
            print("❌ Please enter a valid number.")


def get_decimal_input(prompt: str, required: bool = False) -> Optional[Decimal]:
    """Get decimal input for pricing"""
    while True:
        value = input(f"{prompt}: ").strip()
        
        if not value and not required:
            return None
        
        if not value and required:
            print("❌ This field is required. Please enter a value.")
            continue
        
        try:
            return Decimal(value)
        except Exception:
            print("❌ Please enter a valid decimal number (e.g., 0.50, 1.25).")


def get_yes_no(prompt: str, default: bool = False) -> bool:
    """Get yes/no input"""
    default_str = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{default_str}]: ").strip().lower()
        
        if not value:
            return default
        
        if value in ('y', 'yes'):
            return True
        elif value in ('n', 'no'):
            return False
        else:
            print("❌ Please enter 'y' or 'n'.")


async def seed_roles(session: AsyncSession) -> dict[str, int]:
    """Seed roles table"""
    print_header("ROLES")
    
    roles_data = []
    role_ids = {}
    
    num_roles = get_int_input("How many roles do you want to create?", default=3)
    
    for i in range(num_roles):
        print(f"\n--- Role {i + 1} ---")
        name = get_input(f"Role name (e.g., admin, tenant_admin, user)")
        
        # Check if role already exists
        result = await session.execute(select(Role).where(Role.name == name))
        existing_role = result.scalar_one_or_none()
        
        if existing_role:
            print(f"✓ Role '{name}' already exists with ID: {existing_role.id}")
            role_ids[name] = existing_role.id
        else:
            role = Role(name=name)
            roles_data.append(role)
    
    if roles_data:
        session.add_all(roles_data)
        await session.commit()
        
        for role in roles_data:
            await session.refresh(role)
            role_ids[role.name] = role.id
            print(f"✓ Created role: {role.name} (ID: {role.id})")
    
    return role_ids


async def seed_tenants(session: AsyncSession) -> dict[str, int]:
    """Seed tenants table"""
    print_header("TENANTS")
    
    tenants_data = []
    tenant_ids = {}
    
    num_tenants = get_int_input("How many tenants do you want to create?", default=1)
    
    for i in range(num_tenants):
        print(f"\n--- Tenant {i + 1} ---")
        name = get_input("Tenant name")
        domain = get_input("Tenant domain (unique)")
        logo_url = get_input("Logo URL (optional)", required=False)
        
        # Check if tenant already exists
        result = await session.execute(
            select(Tenant).where((Tenant.name == name) | (Tenant.domain == domain))
        )
        existing_tenant = result.scalar_one_or_none()
        
        if existing_tenant:
            print(f"✓ Tenant '{name}' or domain '{domain}' already exists with ID: {existing_tenant.id}")
            tenant_ids[name] = existing_tenant.id
        else:
            tenant = Tenant(name=name, domain=domain, logo_url=logo_url)
            tenants_data.append(tenant)
    
    if tenants_data:
        session.add_all(tenants_data)
        await session.commit()
        
        for tenant in tenants_data:
            await session.refresh(tenant)
            tenant_ids[tenant.name] = tenant.id
            print(f"✓ Created tenant: {tenant.name} (ID: {tenant.id})")
    
    return tenant_ids


async def seed_llm_models(session: AsyncSession) -> dict[str, int]:
    """Seed llm_models table (Global)"""
    print_header("LLM MODELS")
    
    models_data = []
    model_ids = {}
    
    num_models = get_int_input("How many Global LLM models do you want to create?", default=2)
    
    for i in range(num_models):
        print(f"\n--- LLM Model {i + 1} ---")
        
        name = get_input("Model name (e.g., gpt-4, text-embedding-3-small, chroma)")
        provider = get_input("Provider (e.g., openai, anthropic, chroma)")
        
        # Optional config as JSON
        has_config = get_yes_no("Add configuration JSON?", default=False)
        config = None
        if has_config:
            print("Enter config as JSON (e.g., {'max_tokens': 4000})")
            config_str = input("Config: ").strip()
            if config_str:
                import json
                try:
                    config = json.loads(config_str)
                except json.JSONDecodeError:
                    print("⚠️ Invalid JSON, skipping config")
        
        # Pricing information
        print("\nPricing (per 1M tokens, leave blank if not applicable):")
        input_token_price = get_decimal_input("  Input token price", required=False)
        output_token_price = get_decimal_input("  Output token price", required=False)
        cache_token_price = get_decimal_input("  Cache token price", required=False)
        
        model = LLMModel(
            name=name,
            provider=provider,
            config=config,
            input_token_price=input_token_price,
            output_token_price=output_token_price,
            cache_token_price=cache_token_price,
        )
        models_data.append(model)
        model_ids[name] = None  # Will be updated after commit
    
    if models_data:
        session.add_all(models_data)
        await session.commit()
        
        for model in models_data:
            await session.refresh(model)
            model_ids[model.name] = model.id
            print(f"✓ Created LLM model: {model.name} (ID: {model.id})")
    
    return model_ids


async def seed_users(session: AsyncSession, tenant_ids: dict[str, int], role_ids: dict[str, int]):
    """Seed users table"""
    print_header("USERS")
    
    if not tenant_ids:
        print("⚠️ No tenants available. Skipping users.")
        return
    
    users_data = []
    
    num_users = get_int_input("How many users do you want to create?", default=1)
    
    print("\nAvailable tenants:")
    for idx, (tenant_name, tenant_id) in enumerate(tenant_ids.items(), 1):
        print(f"  {idx}. {tenant_name} (ID: {tenant_id})")
    
    print("\nAvailable roles:")
    for idx, (role_name, role_id) in enumerate(role_ids.items(), 1):
        print(f"  {idx}. {role_name} (ID: {role_id})")
    
    for i in range(num_users):
        print(f"\n--- User {i + 1} ---")
        
        # Select tenant
        tenant_idx = get_int_input(f"Select tenant (1-{len(tenant_ids)})", default=1) - 1
        tenant_name = list(tenant_ids.keys())[tenant_idx]
        tenant_id = tenant_ids[tenant_name]
        
        name = get_input("User name (optional)", required=False)
        email = get_input("Email address")
        
        # Check if user already exists
        result = await session.execute(select(User).where(User.email == email))
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            print(f"✓ User with email '{email}' already exists (ID: {existing_user.id})")
            continue
        
        password = getpass("Password: ")
        password_confirm = getpass("Confirm password: ")
        
        if password != password_confirm:
            print("❌ Passwords do not match. Skipping this user.")
            continue
        
        # Select role
        role_idx = get_int_input(f"Select role (1-{len(role_ids)})", default=1) - 1
        role_name = list(role_ids.keys())[role_idx]
        role_id = role_ids[role_name]
        
        # Email verification
        email_verified = get_yes_no("Is email verified?", default=False)
        verified_at = datetime.utcnow() if email_verified else None
        
        password_hash = get_password_hash(password)
        
        user = User(
            tenant_id=tenant_id,
            name=name,
            email=email,
            password_hash=password_hash,
            role_id=role_id,
            email_verified=email_verified,
            verified_at=verified_at,
        )
        users_data.append(user)
    
    if users_data:
        session.add_all(users_data)
        await session.commit()
        
        for user in users_data:
            await session.refresh(user)
            print(f"✓ Created user: {user.email} (ID: {user.id})")


async def seed_api_keys(session: AsyncSession, tenant_ids: dict[str, int]):
    """Seed api_keys table"""
    print_header("API KEYS")
    
    if not tenant_ids:
        print("⚠️ No tenants available. Skipping API keys.")
        return
    
    api_keys_data = []
    
    num_keys = get_int_input("How many API keys do you want to create?", default=1)
    
    print("\nAvailable tenants:")
    for idx, (tenant_name, tenant_id) in enumerate(tenant_ids.items(), 1):
        print(f"  {idx}. {tenant_name} (ID: {tenant_id})")
    
    for i in range(num_keys):
        print(f"\n--- API Key {i + 1} ---")
        
        # Select tenant
        tenant_idx = get_int_input(f"Select tenant (1-{len(tenant_ids)})", default=1) - 1
        tenant_name = list(tenant_ids.keys())[tenant_idx]
        tenant_id = tenant_ids[tenant_name]
        
        key = get_input("API Key (unique string)")
        
        # Check if API key already exists
        result = await session.execute(select(ApiKey).where(ApiKey.key == key))
        existing_key = result.scalar_one_or_none()
        
        if existing_key:
            print(f"✓ API Key '{key}' already exists (ID: {existing_key.id})")
            continue
        
        api_key = ApiKey(
            tenant_id=tenant_id,
            key=key,
        )
        api_keys_data.append(api_key)
    
    if api_keys_data:
        session.add_all(api_keys_data)
        await session.commit()
        
        for api_key in api_keys_data:
            await session.refresh(api_key)
            print(f"✓ Created API key: {api_key.key} (ID: {api_key.id})")


async def seed_chat_consumers(session: AsyncSession, tenant_ids: dict[str, int]):
    """Seed chat_consumers table"""
    print_header("CHAT CONSUMERS")
    
    if not tenant_ids:
        print("⚠️ No tenants available. Skipping chat consumers.")
        return
    
    chat_consumers_data = []
    
    num_consumers = get_int_input("How many chat consumers do you want to create?", default=1)
    
    print("\nAvailable tenants:")
    for idx, (tenant_name, tenant_id) in enumerate(tenant_ids.items(), 1):
        print(f"  {idx}. {tenant_name} (ID: {tenant_id})")
    
    for i in range(num_consumers):
        print(f"\n--- Chat Consumer {i + 1} ---")
        
        # Select tenant
        tenant_idx = get_int_input(f"Select tenant (1-{len(tenant_ids)})", default=1) - 1
        tenant_name = list(tenant_ids.keys())[tenant_idx]
        tenant_id = tenant_ids[tenant_name]
        
        # Generate or input UUID
        use_generated_uuid = get_yes_no("Generate UUID automatically?", default=True)
        if use_generated_uuid:
            chat_consumer_uuid = uuid4()
            print(f"Generated UUID: {chat_consumer_uuid}")
        else:
            uuid_str = get_input("Enter UUID")
            from uuid import UUID
            try:
                chat_consumer_uuid = UUID(uuid_str)
            except ValueError:
                print("❌ Invalid UUID format. Using generated UUID.")
                chat_consumer_uuid = uuid4()
        
        chat_consumer = ChatConsumer(
            tenant_id=tenant_id,
            chat_consumer_uuid=chat_consumer_uuid,
        )
        chat_consumers_data.append(chat_consumer)
    
    if chat_consumers_data:
        session.add_all(chat_consumers_data)
        await session.commit()
        
        for consumer in chat_consumers_data:
            await session.refresh(consumer)
            print(f"✓ Created chat consumer: {consumer.chat_consumer_uuid} (ID: {consumer.id})")


async def seed_chatbot_configs(session: AsyncSession, tenant_ids: dict[str, int], model_ids: dict[str, int]):
    """Seed chatbot_configs table"""
    print_header("CHATBOT CONFIGS")
    
    if not tenant_ids:
        print("⚠️ No tenants available. Skipping chatbot configs.")
        return
    
    # Store config data before creating objects
    configs_to_create = []
    
    num_configs = get_int_input("How many chatbot configs do you want to create?", default=1)
    
    print("\nAvailable tenants:")
    for idx, (tenant_name, tenant_id) in enumerate(tenant_ids.items(), 1):
        print(f"  {idx}. {tenant_name} (ID: {tenant_id})")
    
    if model_ids:
        print("\nAvailable LLM models (Global):")
        for idx, (model_name, model_id) in enumerate(model_ids.items(), 1):
            print(f"  {idx}. {model_name} (ID: {model_id})")
    
    for i in range(num_configs):
        print(f"\n--- Chatbot Config {i + 1} ---")
        
        # Select tenant
        tenant_idx = get_int_input(f"Select tenant (1-{len(tenant_ids)})", default=1) - 1
        tenant_name = list(tenant_ids.keys())[tenant_idx]
        tenant_id = tenant_ids[tenant_name]
        
        # Chatbot identification fields
        name = get_input("Chatbot name (required, unique per tenant)")
        title = get_input("Chatbot title (optional)", required=False)
        welcome_message = get_input("Welcome message (optional)", required=False)
        bot_avatar = get_input("Bot avatar URL (optional)", required=False)
        is_default = get_yes_no("Set as default chatbot for this tenant?", default=False)
        
        # Check if chatbot with same name already exists for this tenant
        result = await session.execute(
            select(ChatbotConfig).where(
                ChatbotConfig.tenant_id == tenant_id,
                ChatbotConfig.name == name
            )
        )
        existing_config = result.scalar_one_or_none()
        
        if existing_config:
            print(f"✓ Chatbot config with name '{name}' already exists for tenant ID {tenant_id} (Config ID: {existing_config.id})")
            continue
        
        system_prompt = get_input("System prompt (optional)", required=False)
        
        # Model selection
        chat_model_id = None
        embedding_model_id = None
        vec_db_id = None
        
        if model_ids:
            if get_yes_no("Assign chat model?", default=False):
                model_idx = get_int_input(f"Select chat model (1-{len(model_ids)})") - 1
                model_name = list(model_ids.keys())[model_idx]
                chat_model_id = model_ids[model_name]
            
            if get_yes_no("Assign embedding model?", default=False):
                model_idx = get_int_input(f"Select embedding model (1-{len(model_ids)})") - 1
                model_name = list(model_ids.keys())[model_idx]
                embedding_model_id = model_ids[model_name]
            
            if get_yes_no("Assign vector database?", default=False):
                model_idx = get_int_input(f"Select vector DB (1-{len(model_ids)})") - 1
                model_name = list(model_ids.keys())[model_idx]
                vec_db_id = model_ids[model_name]
        
        # Configuration parameters
        print("\nConfiguration Parameters (press Enter to skip):")
        search_type = get_input("Search type (e.g., similarity, mmr)", required=False)
        threshold_value = get_float_input("Threshold value", required=False)
        temperature = get_float_input("Temperature (0.0-1.0)", required=False)
        
        # Performance knobs
        print("\nPerformance Settings:")
        chat_max_tokens = get_int_input("Chat max tokens", required=False, default=4000)
        rag_top_k = get_int_input("RAG top K", required=False, default=5)
        rag_max_history = get_int_input("RAG max history", required=False, default=10)
        rag_context_chars = get_int_input("RAG context chars", required=False, default=4000)
        rag_snippet_chars = get_int_input("RAG snippet chars", required=False, default=200)
        openai_timeout_s = get_int_input("OpenAI timeout (seconds)", required=False, default=30)
        
        # Document processing
        print("\nDocument Processing:")
        chunk_size = get_int_input("Chunk size", required=False, default=1000)
        chunk_overlap = get_int_input("Chunk overlap", required=False, default=200)
        vector_store_type = get_input("Vector store type", required=False, default="qdrant")
        
        # Store all data for later creation
        configs_to_create.append({
            "tenant_id": tenant_id,
            "name": name,
            "title": title,
            "welcome_message": welcome_message,
            "bot_avatar": bot_avatar,
            "is_default": is_default,
            "config": {
                "system_prompt": system_prompt,
                "chat_model_id": chat_model_id,
                "embedding_model_id": embedding_model_id,
                "vec_db_id": vec_db_id,
                "search_type": search_type,
                "threshold_value": threshold_value,
                "temperature": temperature,
                "chat_max_tokens": chat_max_tokens,
                "rag_top_k": rag_top_k,
                "rag_max_history": rag_max_history,
                "rag_context_chars": rag_context_chars,
                "rag_snippet_chars": rag_snippet_chars,
                "openai_timeout_s": openai_timeout_s,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "vector_store_type": vector_store_type,
            }
        })
    
    # Now create all chatbot configs with version history
    configs_data = []
    for config_data in configs_to_create:
        # If setting as default, unset other defaults for this tenant
        if config_data["is_default"]:
            result = await session.execute(
                select(ChatbotConfig).where(
                    ChatbotConfig.tenant_id == config_data["tenant_id"],
                    ChatbotConfig.is_default == True  # noqa: E712
                )
            )
            existing_defaults = result.scalars().all()
            for default_config in existing_defaults:
                default_config.is_default = False
        
        # Create chatbot config with basic fields
        chatbot_config = ChatbotConfig(
            tenant_id=config_data["tenant_id"],
            name=config_data["name"],
            title=config_data["title"],
            welcome_message=config_data["welcome_message"],
            bot_avatar=config_data["bot_avatar"],
            is_default=config_data["is_default"],
        )
        configs_data.append(chatbot_config)
    
    if configs_data:
        # Add all configs to get IDs
        session.add_all(configs_data)
        await session.flush()  # Flush to get IDs
        
        # Now create config_version_history for each chatbot
        for idx, chatbot_config in enumerate(configs_data):
            config_dict = configs_to_create[idx]["config"].copy()
            config_dict["id"] = chatbot_config.id
            config_dict["name"] = chatbot_config.name
            config_dict["title"] = chatbot_config.title
            
            # Create version 0 snapshot
            initial_version = {
                "version": 0,
                "timestamp": datetime.utcnow().isoformat(),
                "config": config_dict,
            }
            
            chatbot_config.config_version_history = [initial_version]
        
        await session.commit()
        
        for config in configs_data:
            await session.refresh(config)
            print(f"✓ Created chatbot config: {config.name} for tenant ID {config.tenant_id} (Config ID: {config.id})")


async def main():
    """Main seeding function"""
    print_header("DATABASE SEEDING SCRIPT")
    print("\nThis script will help you seed the database with initial data.")
    print("All data will be entered interactively.")
    print("\n⚠️  WARNING: This script connects to the database specified in your .env file.")
    print("    Make sure you're connecting to the correct database!")
    
    # Load settings
    try:
        settings = Settings()
    except Exception as e:
        print(f"\n❌ Error loading settings: {e}")
        print("Make sure your .env file is properly configured.")
        sys.exit(1)
    
    print(f"\nDatabase: {settings.POSTGRES_DB}")
    print(f"Host: {settings.POSTGRES_SERVER}")
    print(f"Port: {settings.POSTGRES_PORT}")
    
    if not get_yes_no("\nDo you want to continue?", default=False):
        print("\n❌ Seeding cancelled.")
        sys.exit(0)
    
    # Create async engine
    database_url = (
        f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Seed in dependency order
            role_ids = await seed_roles(session)
            tenant_ids = await seed_tenants(session)
            # LLM models no longer need tenant_ids
            model_ids = await seed_llm_models(session)
            await seed_users(session, tenant_ids, role_ids)
            await seed_api_keys(session, tenant_ids)
            await seed_chat_consumers(session, tenant_ids)
            await seed_chatbot_configs(session, tenant_ids, model_ids)
            
        print_header("SEEDING COMPLETE")
        print("\n✅ Database seeding completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
