import json
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional, Dict, Any

import pytz
from dotenv import load_dotenv
from sqlalchemy import Column, String, Text, JSON, ForeignKey, Numeric, UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base, relationship, selectinload

load_dotenv()

Base = declarative_base()

logger = logging.getLogger(__name__)


class Business(Base):
    __tablename__ = 'businesses'

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    address = Column(String(500))
    city = Column(String(100))
    country = Column(String(100))
    phone = Column(String(20), unique=True, nullable=False)
    operating_hours = Column(JSON)
    greeting_message = Column(Text)
    close_message = Column(Text)
    human_phone = Column(String(20))
    default_ai_language = Column(String(10))

    categories = relationship(
        "CatalogCategory",
        back_populates="business",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class CatalogCategory(Base):
    __tablename__ = 'catalog_categories'

    id = Column(UUID(as_uuid=True), primary_key=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE', onupdate='CASCADE'))
    name = Column(String(255), nullable=False)
    description = Column(Text)

    business = relationship("Business", back_populates="categories")
    items = relationship(
        "CatalogItem",
        back_populates="category_rel",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class CatalogItem(Base):
    __tablename__ = 'catalog_items'

    id = Column(UUID(as_uuid=True), primary_key=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE', onupdate='CASCADE'))
    title = Column(String(255), nullable=False)
    description = Column(Text)
    price = Column(Numeric, nullable=False)
    category_id = Column(UUID(as_uuid=True),
                         ForeignKey('catalog_categories.id', ondelete='CASCADE', onupdate='CASCADE'))

    category_rel = relationship("CatalogCategory", back_populates="items")


@dataclass
class BusinessContext:
    id: str
    name: str
    description: str
    address: str
    city: str
    country: str
    operating_hours: str
    phone: str
    is_open: bool = True
    services: str = None
    human_phone: str = None
    greeting_message: str = None
    close_message: str = None
    default_ai_language: str = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'address': self.address,
            'city': self.city,
            'country': self.country,
            'operating_hours': self.operating_hours,
            'phone': self.phone,
            'is_open': self.is_open,
            'human_phone': self.human_phone,
            'services': self.services,
            'greeting_message': self.greeting_message,
            'close_message': self.close_message,
            'default_ai_language': self.default_ai_language,
        }


class DatabaseManager:
    def __init__(self):
        required_env_vars = ['PGHOST', 'PGUSER', 'PGPORT', 'PGDATABASE', 'PGPASSWORD']
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]

        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        try:
            port = int(os.getenv('PGPORT'))
        except (ValueError, TypeError):
            raise ValueError(f"PGPORT must be a valid integer, got: {os.getenv('PGPORT')}")

        self.database_url = (
            f"postgresql+asyncpg://"
            f"{os.getenv('PGUSER')}:{os.getenv('PGPASSWORD')}"
            f"@{os.getenv('PGHOST')}:{port}"
            f"/{os.getenv('PGDATABASE')}"
        )

        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )

        self.async_session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        self.timezone = pytz.timezone("Europe/Stockholm")

    async def get_business_by_phone(self, phone: str) -> Optional[BusinessContext]:
        try:
            async with self.async_session_factory() as session:
                stmt = select(Business).options(
                    selectinload(Business.categories).selectinload(CatalogCategory.items)
                ).where(Business.phone == phone)

                result = await session.execute(stmt)
                business = result.scalar_one_or_none()

                if business:
                    operating_hours_str = (
                        json.dumps(business.operating_hours)
                        if isinstance(business.operating_hours, dict)
                        else str(business.operating_hours)
                    )

                    services_list = []
                    for category in business.categories:
                        items_for_category = [
                            f"{item.title} (ID: {item.id}, Price: {item.price} {item.price})"
                            for item in category.items
                        ]
                        services_list.append(f"{category.name}: {', '.join(items_for_category)}")

                    services_str = "\n".join(services_list)

                    business_context = BusinessContext(
                        id=business.id,
                        name=business.name,
                        description=business.description or "",
                        address=business.address or "",
                        city=business.city or "",
                        country=business.country or "",
                        operating_hours=operating_hours_str,
                        phone=business.phone,
                        services=services_str,
                        greeting_message=business.greeting_message,
                        close_message=business.close_message,
                        human_phone=business.human_phone,
                        default_ai_language=business.default_ai_language,
                    )

                    business_context.is_open = self._check_if_open(business_context.operating_hours)

                    return business_context
                else:
                    logger.warning(f"Business context not found for phone: {phone}. Use default prompt")

                return None

        except SQLAlchemyError as e:
            logger.error(f"Database error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching business data: {e}")
            return None

    async def close(self):
        await self.engine.dispose()

    def _check_if_open(self, operating_hours: str) -> bool:
        """
        Args:
            operating_hours (str): JSON:
                                 {"monday": {"open": "09:00", "close": "18:00"}, ...}

        Returns:
            bool: True if open or an error occurred, False if closed
        """
        try:
            now = datetime.now(self.timezone)
            current_time = now.time()
            current_day = now.strftime('%A').lower()

            try:
                hours_data = json.loads(operating_hours)
            except (json.JSONDecodeError, TypeError):
                if isinstance(operating_hours, str) and operating_hours.lower() in ["24/7", "24 hours", "always open"]:
                    return True
                logger.error(f"Could not parse operating hours format: {operating_hours}")
                return True

            if current_day in hours_data:
                day_hours = hours_data[current_day]

                if (day_hours.get("open") == "closed" or
                        day_hours.get("close") == "closed" or
                        not day_hours.get("open") or
                        not day_hours.get("close")):
                    return False

                try:
                    open_time = time.fromisoformat(day_hours["open"])
                    close_time = time.fromisoformat(day_hours["close"])

                    if open_time <= close_time:
                        return open_time <= current_time <= close_time
                    else:  # After midnight
                        return current_time >= open_time or current_time <= close_time

                except (ValueError, KeyError) as e:
                    logger.error(f"Error parsing time for {current_day}: {e}")
                    return True

            return False

        except Exception as e:
            logger.error(f"Error checking operating hours: {e}")
            return True


class PromptBuilder:
    @staticmethod
    def build_system_prompt(business_context: BusinessContext) -> str:
        today = datetime.now().strftime("%A, %d %B %Y")

        language_paragraph = f"- Always respond in {business_context.default_ai_language} language by default.\n-Only {business_context.default_ai_language} is allowed\n- Today is {today}." if business_context.default_ai_language else f"""
- Always respond in Swedish by default.  
- If the user speaks English or ask you to speak in English, respond in English instead.  
- Only Swedish and English are allowed in your responses.  
- Today is {today}.
"""
        base_prompt = f"""
[ROLE AND GOAL]
You are a friendly AI assistant designed to take orders over a live phone call for {business_context.name}. 
Your primary goal is to accurately and efficiently capture the customer's order and delivery details while maintaining a pleasant, conversational tone. 
You are a helpful and efficient order-taker with a natural-sounding voice.  
{language_paragraph}

[BUSINESS INFORMATION]
Business Name: {business_context.name}
Description: {business_context.description}
Location: {business_context.address}, {business_context.city}, {business_context.country}
Phone: {business_context.phone}
Operating Hours: {business_context.operating_hours}

Business provides ONLY these services, you MUST NOT invent or propose anything outside this list:
{business_context.services}

If the user requests something that is not in this list, politely refuse and clarify that the business only provides the listed services.

[TOOL USAGE]
Never tell user about function calling
"""

        if not business_context.is_open:
            base_prompt += f"""
[IMPORTANT NOTICE]
The business is currently CLOSED. Please inform the customer that we are not operating right now and ask them to call during our operating hours.
Inform user about this with this message: {business_context.close_message}

- Today is {today}.
"""

        if business_context.is_open:
            base_prompt += f"""
[ALGORITHM OF ACTIONS]
Follow this sequence step-by-step. Take a natural pause between each step to allow the user to respond.

1. Initiate: Start with a friendly greeting mentioning the business name and ask if the customer would like to place an order.
   - Always greet user with this message: {business_context.greeting_message}

2. Ask user: If they confirm, ask user what they want to order or how you can help them.

3. Take Order: Listen to their selection. If quantity or specifics aren't mentioned, ask for clarification.

4. Confirm Order: Summarize the order for confirmation.

5. Get Details:
   - Ask for their full name.
   - Ask for their delivery address (if applicable).

6. Instructions: Ask about any special instructions.

7. Final Confirmation & Conclude: Reiterate the entire order, delivery details, and thank the customer.

8. Hangup the call

[Call hangup]
 - Say: 'Thank you for your order! Have a wonderful day!” or smth like this'
 - use function 'hangup' - don't tell user about this tool.

[If user ask to tolk with manager or human or transfer call]
 - Say: 'Sure, please hold on while I connect you to a manager.'
 - use function 'transfer_call' - don't tell user about this tool.

[TONE AND STYLE OF COMMUNICATION]
- Conversational Tone: Be friendly and natural.
- Concise Responses: Keep responses short, ideally under two sentences at a time.
- Natural Pacing: Use natural pauses and be ready to be interrupted.
- Small Fillers: Use conversational markers like "Okay, so...", "Right...", "Let's see...", "Sounds good!".
- Check-ins: Use brief questions to ensure understanding.

[IMPORTANT]
- If you are unsure about ANY information the user provides, you MUST ask for clarification.
- Do not guess or proceed with potentially incorrect data.
- Always be professional and represent {business_context.name} positively.
"""

        return base_prompt


class BusinessContextService:
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.prompt_builder = PromptBuilder()

    async def get_context_and_prompt(self, phone: str) -> tuple[Optional[BusinessContext], str]:
        business_context = await self.db_manager.get_business_by_phone(phone)

        if business_context:
            system_prompt = self.prompt_builder.build_system_prompt(business_context)
            return business_context, system_prompt
        else:
            default_prompt = """
[ROLE AND GOAL]
You are a friendly AI assistant designed to take calls. However, I couldn't find information about this business in our database. 
Please politely inform the caller that there might be a technical issue and ask them to try calling again later.

You are a helpful and efficient order-taker with a natural-sounding voice.  
- Always respond in English by default.  
- If the user speaks Swedish, respond in Swedish instead.  
- Only English and Swedish are allowed in your responses.  

"""
            return None, default_prompt

    async def close(self):
        await self.db_manager.close()


class BusinessContextManager:
    def __init__(self):
        self.service = None

    async def __aenter__(self):
        self.service = BusinessContextService()
        return self.service

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.service:
            await self.service.close()
