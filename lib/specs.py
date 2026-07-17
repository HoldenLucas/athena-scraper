import os

import requests
import yaml
from termcolor import colored

baseUrl = "https://docs.athenahealth.com/v1/api/swagger/exploreDocs?urlAlias=/api-ref/"
output_dir = "output/"


def fetchSpec(endpoint, category=None):
    # Construct the full URL
    url = f"{baseUrl}{endpoint}"
    outputPath = os.path.join(output_dir, category or "")
    filename = endpoint + ".yaml"
    fullOutputPath = os.path.join(outputPath, filename)

    # Check if the file already exists
    if os.path.exists(fullOutputPath):
        print(colored("✓", "yellow"))
        return

    # Make the HTTP request
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()  # This will raise an error for HTTP errors

        # Parse the JSON response and re-serialize as YAML
        spec = response.json()
        formatted = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)

        # Save the content to a file
        if not os.path.exists(outputPath):
            os.makedirs(outputPath)

        with open(fullOutputPath, "w") as file:
            file.write(formatted)

        print(colored("✓", "green"))

    except requests.exceptions.HTTPError as err:
        print(colored(f"HTTP Error: {err}", "red"))
    except requests.exceptions.RequestException as e:
        print(colored(f"Error: {e}", "red"))
