import os
import aiohttp
import asyncio
from typing import List, Dict

from dotenv import load_dotenv

load_dotenv()

class RecipeFinder:
    # Bing Search API endpoint and subscription key.
    # Replace 'YOUR_BING_API_KEY' with your actual key.
    BING_SEARCH_URL = "https://api.bing.microsoft.com/v7.0/search"
    BING_API_KEY = os.getenv("BING_SEARCH_V7_API_KEY")

    @staticmethod
    async def find_recipe(cuisine: str, ingredients: List[str]) -> List[Dict]:
        # Construct the query to search within seriouseats.com for recipes.
        # The query includes the cuisine, the word 'recipe', and the ingredients.
        ingredients_query = " ".join(ingredients)
        query = f"site:seriouseats.com {cuisine} recipe {ingredients_query}"

        headers = {"Ocp-Apim-Subscription-Key": RecipeFinder.BING_API_KEY}
        params = {
            "q": query,
            "count": 5,               # limit results to 5 (adjust as needed)
            "textDecorations": "True",
            "textFormat": "HTML"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(RecipeFinder.BING_SEARCH_URL, headers=headers, params=params) as response:
                if response.status != 200:
                    raise Exception(f"Bing search API error: {response.status}")
                data = await response.json()

                # Check if the response contains web page results.
                if "webPages" not in data or "value" not in data["webPages"]:
                    return []

                # Extract recipe information from the results.
                recipes = []
                for item in data["webPages"]["value"]:
                    recipes.append({
                        "name": item.get("name"),
                        "url": item.get("url"),
                        "snippet": item.get("snippet")
                    })
                return recipes

# ## Example usage:
# async def main():
#     # Example query: Italian recipes with tomato, basil, and mozzarella
#     cuisine = "Italian"
#     ingredients = ["tomato", "basil", "mozzarella", "pasta"]

#     try:
#         recipes = await RecipeFinder.find_recipe(cuisine, ingredients)
#         if recipes:
#             for recipe in recipes:
#                 print(f"Title: {recipe['name']}\nURL: {recipe['url']}\nSnippet: {recipe['snippet']}\n")
#         else:
#             print("No recipes found.")
#     except Exception as e:
#         print(f"An error occurred: {e}")

# if __name__ == "__main__":
#     asyncio.run(main())