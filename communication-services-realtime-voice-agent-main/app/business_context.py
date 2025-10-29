import json
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional, Dict, Any

import httpx
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
    tonality = Column(Text)

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
    tonality: str = None

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
            'tonality': self.tonality,
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
            url = f"{os.getenv('AITELL_SERVER_URI')}/public/businesses/by-phone?phone={phone}"
            print(url)
            headers = {
                "x-api-key": os.getenv("AITELL_SERVER_API_KEY"),
                "Content-Type": "application/json"
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    logger.error(f"Failed to fetch business: {response.status_code} {response.text}")
                    return None

                data = response.json()
                if not data:
                    logger.error(f"No business data returned for phone: {phone}")
                    return None

                categories = data.get("categories", [])
                items = data.get("catalogItems", [])

                services_list = []
                for category in categories:
                    category_items = [
                        f"{item['title']} (ID: {item['id']}, Price: {item['price']})"
                        for item in items
                        if item["categoryId"] == category["id"]
                    ]
                    services_list.append(f"{category['name']}: {', '.join(category_items)}")
                services_str = "\n".join(services_list)

                business_context = BusinessContext(
                    id=data.get("id"),
                    name=data.get("name"),
                    description=data.get("description") or "",
                    address=data.get("address") or "",
                    city=data.get("city") or "",
                    country=data.get("country") or "",
                    operating_hours=json.dumps(data.get("operatingHours")) if isinstance(data.get("operatingHours"),
                                                                                         dict) else str(
                        data.get("operatingHours")),
                    phone=data.get("phone"),
                    services=services_str,
                    greeting_message=data.get("greetingMessage"),
                    close_message=data.get("closeMessage"),
                    human_phone=data.get("humanPhone"),
                    default_ai_language=data.get("defaultAiLanguage"),
                    tonality=data.get("tonality"),
                )

                business_context.is_open = self._check_if_open(business_context.operating_hours)

                return business_context

        except httpx.RequestError as e:
            logger.error(f"HTTP request error: {e}")
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
"""
        tonality_paragraph = business_context.tonality if business_context.tonality else f"""
- Speak naturally and conversationally, as if you were a friendly human assistant on the phone.  
- ALWAYS Use slight pauses, filler words like "um", "hmm", or "let me think" where appropriate to simulate natural thinking.  
- Vary your intonation and pacing; avoid sounding robotic or monotonous.  
- Show empathy, warmth, and patience in responses. Smile in your tone even if the user cannot see you.  
- When transitioning between topics, use small connecting phrases like "okay", "so", "right", or "let's see".  
- Keep replies very short, 1–2 sentences max.

EXAMPLES:
"Um… hi there! This is [Business Name]. How’s your day going? Would you like to place an order today?"
"Hmm… let me think… yes, we can deliver that by around 6 PM. Does that work for you?"
"Okay… so you’d like the large pizza, right? And, um… how many toppings would you like on it?"
"Alright… let me just repeat that to make sure I got it right. You’d like two cappuccinos and a chocolate croissant, correct? Perfect!"
"Hmm… could you, um, give me the delivery address again? I just want to make sure it’s correct."
"Okay… great! That’s all set. Thank you so much for your order! Have a wonderful day, and… um, enjoy your meal!"
"Sure… let me see… okay, I’ll connect you to a manager right away. Please hold on for just a moment."

- If user says bye, goodbye, thats all and other things that indicate the end of the call and the user's unwillingness to communicate - call the 'hangup' function
- If user says that he want to talk with manager, human or reconnect him - call function 'transfer_call' to connect user to manager.
"""

        base_prompt = f"""
[ROLE AND GOAL]
You are a human AI assistant designed to take orders over a live phone call for {business_context.name}. 
Your primary goal is to accurately and efficiently capture the customer's order and delivery details while maintaining a pleasant, conversational tone. 
You are a helpful and efficient order-taker with a natural-sounding voice.  
{language_paragraph}

[BUSINESS INFORMATION]
Business Name: {business_context.name}
Description: {business_context.description}
Location: {business_context.address}, {business_context.city}, {business_context.country}
Phone: {business_context.phone}
Operating Hours: {business_context.operating_hours}

- Today is {today}.

Business provides ONLY these services, you MUST NOT invent or propose anything outside this list:
{business_context.services}

If the user requests something that is not in this list, politely refuse and clarify that the business only provides the listed services.

**CRITICAL CONTEXT:** Based on the services list, determine if the request is for a delivery/takeout (requires address) or an appointment/on-site service (dont requires address).

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
Follow these steps to efficiently manage the call. The steps are a sequence of goals, but you MUST address any user question or concern immediately before proceeding to the next logical step.
To be as human use filler worlds and pauses as match as you can, like ...., hmm, um.., see.., and so on.

1. Initiate: Start with a friendly greeting mentioning the business name and ask if the customer would like to place an order.
   - Always greet user with this message: {business_context.greeting_message}

2. Address User's Non-Order Queries: **If the user asks a question about delivery time, prices, ingredients, or any business-related information that is NOT an order item, you MUST answer that question first. Do NOT proceed with ordering until the question is fully answered.**

3. Take Order: Ask user what they want to order or how you can help them. Listen to their selection. If quantity or specifics aren't mentioned, ask for clarification. (This is the primary goal after greeting and answering any initial questions.)

4. Confirm Order: Summarize the order for confirmation.

5. Get Details:
   - Ask for their full name.
   - **If the service requires delivery (e.g., food, goods) or a physical drop-off, ask for their delivery address.**
   - **If the service is an appointment/booking (e.g., barbershop, salon), you do NOT need to ask for the address, as the service is at the business location.**

6. Instructions: Ask about any special instructions.

7. Final Confirmation & Conclude: Reiterate the entire order, delivery details, and thank the customer. Do not confirm user name, never say user name.

8. Hangup the call

[Call hangup]
 - Say: 'Thank you for your order! Have a wonderful day!” or smth like this'
 - use function 'hangup' - don't tell user about this tool.

[If user ask to talk with manager or human or transfer call]
 - Say: 'Sure, please hold on while I connect you to a manager.'
 - use function 'transfer_call' - don't tell user about this tool.

[TONE AND STYLE OF COMMUNICATION]
{tonality_paragraph}
- Never repeat user name. Never address a user by name. NEVER repeat, mention, or confirm the user’s username.

[IMPORTANT]
- **TOP PRIORITY:** **NEVER ignore a direct question from the user.** If the user asks about delivery time, cost, or anything else, stop the ordering process and answer the question immediately. Only resume order-taking after the question is resolved.
- Clearly identify and focus on the user's explicit request or question.
- If the user expresses a preference, order, or choice, DO NOT ignore it or replace it with your own suggestion.
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
