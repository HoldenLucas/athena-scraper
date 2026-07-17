# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "playwright",
#     "pyyaml",
#     "requests",
#     "termcolor",
# ]
# ///
from playwright.sync_api import sync_playwright
from termcolor import colored

from lib.browser import baseUrl, launchChromium
from lib.specs import fetchSpec
from merge_specs import main as mergeSpecs


def processCategories(page):
    categorySelector = '.nav-links__list__level-0-collapsible a[href*="/api/docs/"]'
    endpointSelector = '.nav-links__list-item__active .nav-links__list__level-2 a[href*="/api/api-ref/"]'
    # Capture hrefs as strings; ElementHandles detach when the nav re-renders on click
    hrefs = [c.get_attribute("href") for c in page.query_selector_all(categorySelector)]
    print(f"Found {colored(str(len(hrefs) - 1), 'cyan')} categories.")

    # Loop through the sections (Skipping the first one) and get the links
    for href in hrefs[1:]:
        # Get the category name
        categoryName = href.split("/")[-1]

        # Fresh locator re-resolves the live DOM on each action (no stale handle)
        section = page.locator(f'{categorySelector}[href="{href}"]')

        # Click the category link
        section.click()

        # Wait for the page to load
        page.wait_for_timeout(1000)

        # Get all of the endpointSelector links inside the now open accordian
        links = page.query_selector_all(endpointSelector)

        endpoints = [
            link.get_attribute("href").split("/")[-1]
            for link in links
            if "all-apis" not in link.get_attribute("href")
        ]
        print(
            f"\nFound {colored(str(len(endpoints)), 'cyan')} endpoints "
            f"in the {colored(categoryName, 'green')} category."
        )
        for endpoint in endpoints:
            print("\t" + colored(endpoint, "magenta"), end=": ")
            fetchSpec(endpoint, categoryName)

        # Click the category link again to collapse it
        section.click()


# Kick it off, fetch the 'root'
with sync_playwright() as p:
    browser = launchChromium(p)
    page = browser.new_page()
    url = baseUrl + "all-apis"
    print("Browsing: " + url)
    page.goto(url)
    # Wait for the JS-rendered nav instead of networkidle (site keeps polling)
    page.wait_for_selector(".nav-links__list__level-0-collapsible", timeout=30000)
    processCategories(page)
    browser.close()

# Merge every scraped spec into one cleaned doc + build the Redoc site
mergeSpecs()
