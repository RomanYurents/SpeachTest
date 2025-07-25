
# Azure Communication Services RealTime Voice Agent

This is a sample that will enable you to build a realtime voice agent using Azure Communication Services

## Prerequisites

- An Azure account with an active subscription. [Create an account for free](https://azure.microsoft.com/free/?WT.mc_id=A261C142F). 
- A deployed Communication Services resource. [Create a Communication Services resource](https://docs.microsoft.com/azure/communication-services/quickstarts/create-communication-resource).
- A [phone number](https://learn.microsoft.com/en-us/azure/communication-services/quickstarts/telephony/get-phone-number) in your Azure Communication Services resource that can get inbound calls. NB: phone numbers are not available in free subscriptions.
- [Python](https://www.python.org/downloads/) 3.7 or above.
- An Azure OpenAI Resource and Deployed Model. See [instructions](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal).



### Setup and host your Azure DevTunnel

[Azure DevTunnels](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/overview) is an Azure service that enables you to share local web services hosted on the internet. Use the commands below to connect your local development environment to the public internet. This creates a tunnel with a persistent endpoint URL and which allows anonymous access. We will then use this endpoint to notify your application of calling events from the ACS Call Automation service.

```bash
devtunnel create --allow-anonymous
devtunnel port create -p 8000
devtunnel host
```


### Add the API keys and endpoints in the project

The `.env ` file will contain various API keys and endpoint configurations which are required for this sample, for example to update the OpenAI Connection String update the `AZURE_OPENAI_REALTIME_SERVICE_KEY` value

```bash
AZURE_OPENAI_REALTIME_SERVICE_KEY="<UPDATE_WITH_YOUR_API_KEY>"
```

Ensure that the `ACS_SMS_FROM_PHONE_NUMBER` is the acquired phone number from the ACS resource in the E.164 phone number format. For example:

```bash
ACS_SMS_FROM_PHONE_NUMBER="+18662358536"
```

### Setup and run the Python environment

The command below will automatically install the required python version and package dependencies defined in the `pyprojec.toml` file

```bash
uv sync
```

Run the project locally using the command:

```bash
uv run uvicorn app.main:app --reload
```





    top_recipes = {
        "World's Best Lasagna": {
            "url": "https://www.allrecipes.com/recipe/23600/worlds-best-lasagna/",
            "ingredients": [
                "sweet Italian sausage",
                "lean ground beef",
                "onion",
                "garlic",
                "crushed tomatoes",
                "tomato paste",
                "tomato sauce",
                "water",
                "sugar",
                "dried basil leaves",
                "fennel seeds",
                "Italian seasoning",
                "salt",
                "black pepper",
                "fresh parsley",
                "lasagna noodles",
                "ricotta cheese",
                "egg",
                "mozzarella cheese",
                "Parmesan cheese",
            ],
        },
        "Banana Banana Bread": {
            "url": "https://www.allrecipes.com/recipe/20144/banana-banana-bread/",
            "ingredients": [
                "ripe bananas",
                "melted butter",
                "white sugar",
                "egg",
                "vanilla extract",
                "baking soda",
                "salt",
                "all-purpose flour",
            ],
        },
        "Homemade Mac and Cheese": {
            "url": "https://www.allrecipes.com/recipe/11679/homemade-mac-and-cheese/",
            "ingredients": [
                "elbow macaroni",
                "butter",
                "all-purpose flour",
                "salt",
                "black pepper",
                "milk",
                "shredded Cheddar cheese",
            ],
        },
        "Chicken Pot Pie IX": {
            "url": "https://www.allrecipes.com/recipe/26317/chicken-pot-pie-ix/",
            "ingredients": [
                "cubed skinless, boneless chicken breast",
                "sliced carrots",
                "frozen green peas",
                "sliced celery",
                "butter",
                "chopped onion",
                "all-purpose flour",
                "salt",
                "black pepper",
                "celery seed",
                "chicken broth",
                "milk",
                "unbaked pie crusts",
            ],
        },
        "Easy Meatloaf": {
            "url": "https://www.allrecipes.com/recipe/16354/easy-meatloaf/",
            "ingredients": [
                "ground beef",
                "egg",
                "onion",
                "milk",
                "bread crumbs",
                "salt",
                "black pepper",
                "brown sugar",
                "prepared mustard",
                "ketchup",
            ],
        },
        "Zuppa Toscana": {
            "url": "https://www.allrecipes.com/recipe/143069/zuppa-toscana/",
            "ingredients": [
                "bulk mild Italian sausage",
                "crushed red pepper flakes",
                "diced bacon",
                "onion",
                "garlic",
                "chicken broth",
                "water",
                "potatoes",
                "heavy cream",
                "fresh kale",
            ],
        },
        "Oven-Roasted Asparagus": {
            "url": "https://www.allrecipes.com/recipe/214931/oven-roasted-asparagus/",
            "ingredients": [
                "asparagus",
                "olive oil",
                "Parmesan cheese",
                "garlic",
                "salt",
                "black pepper",
                "lemon juice",
            ],
        },
        "Slow Cooker Pulled Pork": {
            "url": "https://www.allrecipes.com/recipe/228823/slow-cooker-pulled-pork/",
            "ingredients": [
                "pork shoulder roast",
                "bottled barbecue sauce",
                "apple cider vinegar",
                "chicken broth",
                "brown sugar",
                "yellow mustard",
                "Worcestershire sauce",
                "chili powder",
                "onion",
                "garlic",
                "thyme",
                "salt",
                "black pepper",
                "cayenne pepper",
                "hamburger buns",
            ],
        },
        "Baked Ziti I": {
            "url": "https://www.allrecipes.com/recipe/23600/baked-ziti-i/",
            "ingredients": [
                "ziti pasta",
                "onion",
                "lean ground beef",
                "garlic",
                "marinara sauce",
                "ricotta cheese",
                "mozzarella cheese",
                "egg",
                "grated Parmesan cheese",
            ],
        },
        "Banana Muffins II": {
            "url": "https://www.allrecipes.com/recipe/7067/banana-muffins-ii/",
            "ingredients": [
                "all-purpose flour",
                "baking powder",
                "baking soda",
                "salt",
                "ripe bananas",
                "white sugar",
                "egg",
                "melted butter",
            ],
        },
    }
