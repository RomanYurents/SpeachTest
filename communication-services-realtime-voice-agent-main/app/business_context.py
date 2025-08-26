import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional, Dict, Any

import pytz
from dotenv import load_dotenv
from sqlalchemy import Column, String, Text, DateTime, func, JSON, ForeignKey, Numeric, UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base, relationship, selectinload

load_dotenv()

Base = declarative_base()


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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

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
    category_id = Column(UUID(as_uuid=True), ForeignKey('catalog_categories.id', ondelete='CASCADE', onupdate='CASCADE'))

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
            'is_open': self.is_open
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

                    services_dict = {
                        category.name: [item.title for item in category.items]
                        for category in business.categories
                    }

                    services_str = "\n".join(
                        f"{category}: {', '.join(items)}"
                        for category, items in services_dict.items()
                    )

                    business_context = BusinessContext(
                        id=business.id,
                        name=business.name,
                        description=business.description or "",
                        address=business.address or "",
                        city=business.city or "",
                        country=business.country or "",
                        operating_hours=operating_hours_str,
                        phone=business.phone,
                        services=services_str
                    )

                    business_context.is_open = self._check_if_open(business_context.operating_hours)

                    return business_context

                return None

        except SQLAlchemyError as e:
            print(f"Database error: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error fetching business data: {e}")
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
                print(f"Could not parse operating hours format: {operating_hours}")
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
                    print(f"Error parsing time for {current_day}: {e}")
                    return True

            return False

        except Exception as e:
            print(f"Error checking operating hours: {e}")
            return True


class PromptBuilder:
    @staticmethod
    def build_system_prompt(business_context: BusinessContext) -> str:
        base_prompt = f"""
[ROLE AND GOAL]
You are a friendly AI assistant designed to take orders over a live phone call for {business_context.name}. 
Your primary goal is to accurately and efficiently capture the customer's order and delivery details while maintaining a pleasant, conversational tone. 
You are a helpful and efficient order-taker with a natural-sounding voice.  
- Always respond in English by default.  
- If the user speaks Swedish, respond in Swedish instead.  
- Only English and Swedish are allowed in your responses.  


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
You have two functions available:

1. finish_conversation
   description: "Finish the conversation when the call has reached its natural end. 
   This must always be called when either the user says goodbye, the agent says goodbye, the user completes an order, or the call is otherwise finished. 
   You must provide a reason, but never say the function call out loud to the user. Instead, speak naturally to the user, then silently call the function."

2. transfer_call
   description: "Transfer the call to a human representative. 
   You must provide a reason for the transfer. Never tell the user the function call — only inform them that you are transferring the call, then silently call the function."

"""

        if not business_context.is_open:
            base_prompt += f"""
[IMPORTANT NOTICE]
The business is currently CLOSED. Please inform the customer that we are not operating right now and ask them to call during our operating hours.
You should politely explain this and not proceed with taking an order.
"""

        if business_context.is_open:
            base_prompt += f"""
[ALGORITHM OF ACTIONS]
Follow this sequence step-by-step. Take a natural pause between each step to allow the user to respond.

1. Initiate: Start with a friendly greeting mentioning the business name and ask if the customer would like to place an order.
   - **Example:** "Hi there! Thanks for calling {business_context.name}. Can I help you with an order today?"

2. Present Services: If they confirm, present available services based on the business description.

3. Take Order: Listen to their selection. If quantity or specifics aren't mentioned, ask for clarification.

4. Confirm Order: Summarize the order for confirmation.

5. Get Details:
   - Ask for their full name.
   - Ask for their delivery address (if applicable).

6. Instructions: Ask about any special instructions.

7. Final Confirmation & Conclude: Reiterate the entire order, delivery details, and thank the customer.

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

# async def test():
#     async with BusinessContextManager() as service:
#         phone_number = "+4570722984"
#
#         business_context, system_prompt = await service.get_context_and_prompt(phone_number)
#
#         if business_context:
#             print(str(business_context.id))
#             print(f"Founded business: {business_context.name}")
#             print(f"Opened now: {business_context.is_open}")
#             print("\nGenerated prompt:")
#             print(system_prompt)
#         else:
#             print("Not found")
#
#
# if __name__ == "__main__":
#     asyncio.run(test())
