import requests
import json
import os

BASE_URL = "https://api.datto.com/v1/saas"

def get_domains(public_key, secret_key, save_to_file=True, output_dir=None):
    """
    Fetches the list of SaaS Protection domains for the given API credentials.
    Optionally saves the raw response to domains.json.
    Returns the parsed JSON data.
    """
    response = requests.get(
        f"{BASE_URL}/domains",
        auth=(public_key, secret_key)
    )
    response.raise_for_status()  # raises an error if status isn't 2xx, instead of silently continuing

    data = response.json()

    if save_to_file:
        folder = output_dir or os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(folder, "domains.json")
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Saved response to {output_path}")

    return data


if __name__ == "__main__":
    # Lets you still run this file directly for a quick manual test
    test_public = "your_public_key_here"
    test_secret = "your_secret_key_here"
    get_domains(test_public, test_secret)