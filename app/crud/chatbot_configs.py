"""
CRUD operations for ChatbotConfig model.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chatbot_configs import ChatbotConfig
from app.schemas.chatbot_configs import ChatbotConfigCreate, ChatbotConfigUpdate


class ChatbotConfigCRUD:
    """CRUD operations for ChatbotConfig model"""

    async def create(
        self, db: AsyncSession, chatbot_config_create: ChatbotConfigCreate
    ) -> ChatbotConfig:
        """Create new chatbot configuration"""
        # If this is set as default, unset other defaults for the tenant
        if chatbot_config_create.is_default:
            await self._unset_other_defaults(db, chatbot_config_create.tenant_id)

        db_chatbot_config = ChatbotConfig(
            tenant_id=chatbot_config_create.tenant_id,
            name=chatbot_config_create.name,
            title=chatbot_config_create.title,
            welcome_message=chatbot_config_create.welcome_message,
            bot_avatar=chatbot_config_create.bot_avatar,
            is_default=chatbot_config_create.is_default,
        )
        db.add(db_chatbot_config)
        await db.flush()  # Flush to get the ID
        
        # Create initial version 0 in history from create schema
        # Include all fields (including extra ones) except tenant_id
        # name, title, welcome_message, bot_avatar, is_default should be in config_version_history
        config_dict = chatbot_config_create.model_dump(
            exclude={'tenant_id'},  # Only exclude tenant_id, all other fields go into config_version_history
            exclude_unset=False  # Include all fields, even if not explicitly set
        )
        config_dict['id'] = db_chatbot_config.id
        initial_version = self._create_version_snapshot_from_dict(config_dict, version=0)
        db_chatbot_config.config_version_history = [initial_version]
        
        await db.commit()
        await db.refresh(db_chatbot_config)
        return db_chatbot_config

    async def get(
        self, db: AsyncSession, chatbot_config_id: int
    ) -> ChatbotConfig | None:
        """Get chatbot configuration by ID"""
        return await db.get(ChatbotConfig, chatbot_config_id)

    async def get_with_relationships(
        self, db: AsyncSession, chatbot_config_id: int
    ) -> ChatbotConfig | None:
        """Get chatbot configuration by ID"""
        # Relationships are no longer needed since FK columns are removed
        # Config is stored in version history JSON
        return await self.get(db, chatbot_config_id)

    async def list_by_tenant(
        self, db: AsyncSession, tenant_id: int
    ) -> list[ChatbotConfig]:
        """List all chatbot configurations for a tenant"""
        result = await db.execute(
            select(ChatbotConfig)
            .where(ChatbotConfig.tenant_id == tenant_id)
            .order_by(ChatbotConfig.is_default.desc(), ChatbotConfig.created_at.desc())
        )
        return result.scalars().all()

    async def get_default(
        self, db: AsyncSession, tenant_id: int
    ) -> ChatbotConfig | None:
        """Get default chatbot configuration for a tenant"""
        # First try to find explicit default
        result = await db.execute(
            select(ChatbotConfig).where(
                ChatbotConfig.tenant_id == tenant_id,
                ChatbotConfig.is_default == True,  # noqa: E712
            )
        )
        config = result.scalar_one_or_none()
        
        if config:
            return config
            
        # Fallback: get the latest created config
        result = await db.execute(
            select(ChatbotConfig)
            .where(ChatbotConfig.tenant_id == tenant_id)
            .order_by(ChatbotConfig.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        db: AsyncSession,
        chatbot_config_id: int,
        chatbot_config_update: ChatbotConfigUpdate,
    ) -> ChatbotConfig | None:
        """Update chatbot configuration"""
        db_chatbot_config = await self.get(db, chatbot_config_id)
        if not db_chatbot_config:
            return None

        # If setting as default, unset other defaults
        if chatbot_config_update.is_default is True:
            await self._unset_other_defaults(db, db_chatbot_config.tenant_id)

        # Before updating, create a snapshot of current state and add to version history
        # Only if there are actual changes (exclude_unset=True means only provided fields)
        update_data = chatbot_config_update.model_dump(exclude_unset=True)
        if update_data:  # Only create version if there are actual updates
            # Get current version history or initialize empty list.
            existing_history = db_chatbot_config.config_version_history or []
            # copy for SQLAlchemy change tracking
            version_history = list(existing_history)  
            
            # Determine latest snapshot (highest version) regardless of stored order
            latest_snapshot = None
            if version_history:
                latest_snapshot = max(
                    version_history,
                    key=lambda entry: entry.get("version", 0),
                )
                current_config = latest_snapshot.get("config", {}).copy()
                current_version_index = latest_snapshot.get("version", 0)
            else:
                current_config = {
                    'id': db_chatbot_config.id,
                    'name': db_chatbot_config.name,
                    'title': db_chatbot_config.title,
                    'welcome_message': db_chatbot_config.welcome_message,
                    'bot_avatar': db_chatbot_config.bot_avatar,
                    'is_default': db_chatbot_config.is_default,
                }
                current_version_index = -1
            
            new_version_index = current_version_index + 1
            
            # Merge update data into current config
            current_config.update(update_data)
            current_config['id'] = db_chatbot_config.id
            
            # Create snapshot of updated config state
            new_snapshot = self._create_version_snapshot_from_dict(
                current_config, version=new_version_index
            )
            
            # Insert new snapshot and order history so newest is first
            version_history.append(new_snapshot)
            version_history.sort(
                key=lambda entry: entry.get("version", 0), reverse=True
            )
            db_chatbot_config.config_version_history = version_history

        # Update database columns for backward compatibility and easier querying
        # These fields are also stored in config_version_history
        for field in ['name', 'title', 'welcome_message', 'bot_avatar', 'is_default']:
            if field in update_data:
                setattr(db_chatbot_config, field, update_data[field])

        await db.commit()
        await db.refresh(db_chatbot_config)
        return db_chatbot_config

    async def delete(self, db: AsyncSession, chatbot_config_id: int) -> bool:
        """Delete chatbot configuration"""
        db_chatbot_config = await self.get(db, chatbot_config_id)
        if not db_chatbot_config:
            return False

        # If deleting default, set another one as default if available
        if db_chatbot_config.is_default:
            tenant_id = db_chatbot_config.tenant_id
            await db.delete(db_chatbot_config)
            await db.commit()

            # Set first available chatbot as default
            other_configs = await self.list_by_tenant(db, tenant_id)
            if other_configs:
                other_configs[0].is_default = True
                await db.commit()
        else:
            await db.delete(db_chatbot_config)
            await db.commit()

        return True

    async def set_default(
        self, db: AsyncSession, tenant_id: int, chatbot_config_id: int
    ) -> ChatbotConfig | None:
        """Set a chatbot as default for a tenant"""
        # Verify the chatbot belongs to the tenant
        db_chatbot_config = await self.get(db, chatbot_config_id)
        if not db_chatbot_config or db_chatbot_config.tenant_id != tenant_id:
            return None

        # Unset other defaults
        await self._unset_other_defaults(db, tenant_id)

        # Set this one as default
        db_chatbot_config.is_default = True
        await db.commit()
        await db.refresh(db_chatbot_config)
        return db_chatbot_config

    def _create_version_snapshot_from_dict(
        self, config_dict: dict[str, Any], version: int
    ) -> dict[str, Any]:
        """
        Create a version snapshot of the chatbot configuration from a dictionary.
        
        Args:
            config_dict: Dictionary containing config values
            version: Version number for this snapshot
            
        Returns:
            Dictionary representing the version snapshot
        """
        return {
            "version": version,
            "timestamp": datetime.utcnow().isoformat(),
            "config": config_dict,
        }

    async def _unset_other_defaults(
        self, db: AsyncSession, tenant_id: int
    ) -> None:
        """Unset is_default flag for all chatbots in a tenant"""
        await db.execute(
            update(ChatbotConfig)
            .where(
                ChatbotConfig.tenant_id == tenant_id,
                ChatbotConfig.is_default == True,  # noqa: E712
            )
            .values(is_default=False)
        )
        await db.commit()


chatbot_config = ChatbotConfigCRUD()

