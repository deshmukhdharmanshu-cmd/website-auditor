import os
import json
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import openai
from googlesearch import search
import urllib.parse
from urllib.parse import urljoin

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__, static_folder='static')
CORS(app)

def fetch_html(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None

def auto_discover_funnel(home_url):
    """Attempts to find Category, Cart, and Checkout URLs automatically"""
    funnel = {
        "Home": home_url,
        "Category": None,
        "Cart": None,
        "Checkout": None
    }
    
    html = fetch_html(home_url)
    if not html:
        return funnel
        
    soup = BeautifulSoup(html, 'html.parser')
    parsed_home = urllib.parse.urlparse(home_url)
    base_url = f"{parsed_home.scheme}://{parsed_home.netloc}"
    
    # Guess Category
    category_keywords = ['/collections/', '/category/', '/c/', '/shop/']
    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        if any(kw in href for kw in category_keywords):
            funnel["Category"] = urljoin(base_url, a['href'])
            break
            
    # If no obvious category found, just grab the first valid internal link that isn't root
    if not funnel["Category"]:
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('/') and len(href) > 2:
                funnel["Category"] = urljoin(base_url, href)
                break
                
    # Guess Cart and Checkout (Standard Shopify/Magento/WooCommerce routes)
    funnel["Cart"] = urljoin(base_url, "/cart")
    funnel["Checkout"] = urljoin(base_url, "/checkout")
    
    return funnel

def extract_deep_data(html_content, url, page_type="General"):
    """Deep extraction including image URLs for visual context"""
    if not html_content:
        return {"error": f"Could not fetch {page_type} page"}
        
    soup = BeautifulSoup(html_content, 'html.parser')
    for script in soup(["script", "style", "noscript", "svg"]):
        script.extract()
        
    # Extract Images for Context
    images = []
    for img in soup.find_all('img', src=True):
        src = img['src']
        if not src.startswith('http'):
            src = urljoin(url, src)
        images.append({"src": src, "alt": img.get('alt', '')})
    images = images[:10] # limit to 10 meaningful images
        
    title = soup.title.string.strip() if soup.title and soup.title.string else "None"
    
    # Hero / Main Content
    h1 = soup.find('h1')
    hero = h1.get_text(strip=True) if h1 else "No H1"
    
    headings = [h.get_text(strip=True) for h in soup.find_all(['h2', 'h3'])][:10]
    ctas = [btn.get_text(strip=True) for btn in soup.find_all(['button', 'a']) if len(btn.get_text(strip=True)) > 2][:10]

    return {
        "url": url,
        "page_type": page_type,
        "title": title,
        "hero_headline": hero,
        "content_structure": headings,
        "calls_to_action": ctas,
        "images_found": images
    }

def find_competitors(home_title):
    competitors = []
    try:
        query = f"top competitors for {home_title} jewelry" 
        results = search(query, num_results=5)
        for url in results:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()
            if any(social in domain for social in ['facebook', 'instagram', 'twitter', 'linkedin', 'youtube', 'pinterest', 'amazon']):
                continue
            clean_url = f"https://{domain}"
            if clean_url not in competitors:
                competitors.append(clean_url)
            if len(competitors) >= 2: # Limit to 2 to save tokens and time
                break
    except Exception as e:
        print(f"Competitor search failed: {e}")
    return competitors

def analyze_section_with_ai(target_data, competitors_data, section_name):
    """Call GPT-4o for a SPECIFIC section to get 10 actionables and insights with images"""
    
    if "error" in target_data:
        return {
            "url": target_data.get("url"),
            "error": target_data["error"]
        }
        
    system_prompt = f"""
    You are evaluating the '{section_name}' page of an e-commerce website.
    You will receive data for this specific page, including URLs to images found on the page.
    
    Output a JSON object with exactly two keys:
    1. "insights": An array of deep UX/CRO audit objects.
       Each object must have:
       - "element": string (e.g., "Hero Banner", "Product Grid", "Checkout Button")
       - "current": string
       - "gap": string
       - "fixes": string
       - "image_url": string (If your critique is about a specific image or banner, MUST copy the EXACT 'src' URL from the 'images_found' data provided to you. If not applicable, use an empty string "").
       
    2. "actionables": An array of EXACTLY 10 strings representing the top 10 recommended actions for THIS SPECIFIC {section_name} PAGE.
    
    Do NOT return markdown formatting (no ```json). Just the raw JSON object.
    """
    
    user_prompt = f"TARGET DATA ({section_name}):\n{json.dumps(target_data, indent=2)}\n\n"
    if competitors_data:
        user_prompt += f"COMPETITOR DATA (For Benchmarking):\n{json.dumps(competitors_data, indent=2)}"
        
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=2500
        )
        
        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```json"):
            result_text = result_text[7:-3]
        elif result_text.startswith("```"):
            result_text = result_text[3:-3]
            
        parsed_result = json.loads(result_text)
        parsed_result["url"] = target_data.get("url")
        return parsed_result
    except Exception as e:
        print(f"OpenAI API Error on {section_name}: {e}")
        return {
            "url": target_data.get("url"),
            "insights": [{"element": "AI Error", "current": "", "gap": str(e), "fixes": "", "image_url": ""}],
            "actionables": ["Retry the analysis"]
        }

@app.route('/')
def serve_index():
    return app.send_static_file('index.html')
@app.route('/<path:path>')
def serve_static(path):
    return app.send_static_file(path)

@app.route('/api/audit', methods=['POST'])
def audit_website():
    data = request.json
    home_url = data.get('home_url')
    
    if not home_url:
        return jsonify({"error": "Home URL is required"}), 400
    if not home_url.startswith('http'):
        home_url = 'https://' + home_url

    try:
        # Step 1: Auto-discover funnel
        funnel_urls = auto_discover_funnel(home_url)
        
        # Step 2: Extract data for each funnel step
        target_funnel_data = {}
        home_title = "Jewelry"
        for section, url in funnel_urls.items():
            if url:
                html = fetch_html(url)
                if html:
                    target_funnel_data[section] = extract_deep_data(html, url, section)
                    if section == "Home":
                        home_title = target_funnel_data[section].get("title", home_title)
                else:
                    target_funnel_data[section] = {"error": "Page blocked or returned 404", "url": url}
            else:
                target_funnel_data[section] = {"error": "Could not auto-discover this URL"}
                
        # Step 3: Find and extract competitors (just homepages to save limits)
        competitor_urls = find_competitors(home_title)
        competitors_data = []
        for c_url in competitor_urls:
            c_html = fetch_html(c_url)
            if c_html:
                competitors_data.append(extract_deep_data(c_html, c_url, "Competitor Home"))
                
        # Step 4: Run AI Analysis per section
        funnel_audit = {}
        sections = ["Home", "Category", "Cart", "Checkout"]
        
        for section in sections:
            # We pass competitor data to every section call so it can benchmark
            funnel_audit[section] = analyze_section_with_ai(target_funnel_data.get(section, {}), competitors_data, section)
            
        return jsonify({
            "target_url": home_url,
            "competitors": competitor_urls,
            "status": "success",
            "funnel_audit": funnel_audit
        })
        
    except Exception as e:
        return jsonify({"error": f"Failed to process audit: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)
