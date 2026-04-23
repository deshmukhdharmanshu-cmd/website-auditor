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


from flask import render_template_string, Response

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Website Auditor</title>
    <link rel="manifest" href="manifest.json">
    <meta name="theme-color" content="#0d0f12">
    <link rel="apple-touch-icon" href="icon-192.png">
    <style>:root {
    --bg-color: #0d0f12;
    --card-bg: rgba(25, 28, 35, 0.6);
    --border-color: rgba(255, 255, 255, 0.08);
    --primary-color: #e2b466; /* Elegant gold for Krishna Pearls */
    --primary-hover: #c99b4f;
    --text-primary: #f0f2f5;
    --text-secondary: #9ca3af;
    --gap-color: #ef4444;
    --fix-color: #10b981;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Inter', sans-serif;
    background-color: var(--bg-color);
    color: var(--text-primary);
    min-height: 100vh;
    display: flex;
    justify-content: center;
    padding: 2rem;
    position: relative;
    overflow-x: hidden;
}

.background-glow {
    position: fixed;
    top: -20%;
    left: 50%;
    transform: translateX(-50%);
    width: 60vw;
    height: 60vw;
    background: radial-gradient(circle, rgba(226, 180, 102, 0.15) 0%, rgba(13, 15, 18, 0) 70%);
    border-radius: 50%;
    z-index: -1;
    pointer-events: none;
}

.container {
    width: 100%;
    max-width: 1000px;
    z-index: 1;
}

.header {
    text-align: center;
    margin-bottom: 3rem;
    animation: fadeIn 0.8s ease-out;
}

.logo {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
}

.logo-icon {
    font-size: 2rem;
}

h1 {
    font-size: 2.5rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    background: linear-gradient(to right, #fff, var(--primary-color));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.subtitle {
    color: var(--text-secondary);
    font-size: 1.1rem;
}

.search-section {
    margin-bottom: 4rem;
    animation: slideUp 0.6s ease-out 0.2s both;
}

.search-box {
    display: flex;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 0.5rem;
    backdrop-filter: blur(12px);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
    transition: border-color 0.3s ease;
}

#homeUrlInput {
    flex: 1;
    background: transparent;
    border: none;
    padding: 1rem 1.5rem;
    color: white;
    font-size: 1rem;
    outline: none;
}

#homeUrlInput::placeholder {
    color: #6b7280;
}

#submitBtn {
    background-color: var(--primary-color);
    color: #000;
    border: none;
    border-radius: 8px;
    padding: 0 2rem;
    font-weight: 600;
    font-size: 1rem;
    cursor: pointer;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 140px;
}

#submitBtn:hover {
    background-color: var(--primary-hover);
    transform: translateY(-1px);
}

#submitBtn:active {
    transform: translateY(1px);
}

.error-message {
    color: var(--gap-color);
    margin-top: 1rem;
    text-align: center;
    font-size: 0.9rem;
}

.hidden {
    display: none !important;
}

.results-section {
    animation: fadeIn 0.5s ease-out;
}

.results-header {
    margin-bottom: 2rem;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 1rem;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
}

.results-header h2 {
    font-size: 1.5rem;
    font-weight: 600;
}

.scanned-url {
    color: var(--primary-color);
    text-decoration: none;
    font-size: 0.9rem;
}

.scanned-url:hover {
    text-decoration: underline;
}

.insights-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1.5rem;
}

.insight-card {
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    overflow: hidden;
    backdrop-filter: blur(8px);
    transition: transform 0.3s ease, box-shadow 0.3s ease;
    display: flex;
    flex-direction: column;
}

.insight-image {
    width: 100%;
    max-height: 200px;
    object-fit: cover;
    border-bottom: 1px solid var(--border-color);
}

.insight-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 24px rgba(0, 0, 0, 0.3);
    border-color: rgba(226, 180, 102, 0.3);
}

.card-header {
    background: rgba(255, 255, 255, 0.03);
    padding: 1rem 1.5rem;
    border-bottom: 1px solid var(--border-color);
}

.element-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: #fff;
}

.card-body {
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
}

.insight-row {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}

.label {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.current-label { color: var(--text-secondary); }
.gap-label { color: var(--gap-color); }
.fix-label { color: var(--fix-color); }

.value {
    font-size: 0.95rem;
    line-height: 1.5;
    color: #d1d5db;
}

.fix-row {
    background: rgba(16, 185, 129, 0.05);
    padding: 1rem;
    border-radius: 8px;
    border: 1px solid rgba(16, 185, 129, 0.1);
}

.scanned-urls {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}

.scanned-url {
    color: var(--primary-color);
    text-decoration: none;
    font-size: 0.85rem;
}

.scanned-url:hover {
    text-decoration: underline;
}

.funnel-section {
    margin-bottom: 5rem;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    padding-top: 3rem;
}

.funnel-section:first-child {
    border-top: none;
    padding-top: 0;
}

.section-title {
    font-size: 2rem;
    font-weight: 700;
    color: var(--primary-color);
    margin-bottom: 0.5rem;
}

.section-url {
    color: var(--text-secondary);
    font-size: 0.9rem;
    margin-bottom: 2rem;
}

.actionables-section {
    margin-top: 3rem;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 2rem;
    backdrop-filter: blur(8px);
}

.actionables-section h3 {
    font-size: 1.4rem;
    margin-bottom: 1.5rem;
    color: #fff;
}

.actionables-list {
    padding-left: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
}

.actionables-list li {
    font-size: 1rem;
    line-height: 1.5;
    color: #d1d5db;
}

.actionables-list li strong {
    color: var(--primary-color);
}

/* Loader Spinner */
.loader {
    width: 20px;
    height: 20px;
    border: 2px solid rgba(0,0,0,0.2);
    border-top-color: #000;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes slideUp {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 600px) {
    body {
        padding: 1rem;
    }
    .search-box {
        flex-direction: column;
        padding: 0.75rem;
    }
    #submitBtn {
        padding: 1rem;
        margin-top: 0.5rem;
    }
    .results-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.5rem;
    }
    h1 {
        font-size: 2rem;
    }
    .insights-grid {
        grid-template-columns: 1fr;
    }
    .actionables-section {
        padding: 1.5rem 1rem;
    }
}
</style>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <div class="background-glow"></div>
    
    <main class="container">
        <header class="header">
            <div class="logo">
                <span class="logo-icon">✨</span>
                <h1>Website Auditor</h1>
            </div>
            <p class="subtitle">Deep funnel analysis and competitor benchmarking.</p>
        </header>

        <section class="search-section">
            <form id="auditForm" class="search-box">
                <input type="url" id="homeUrlInput" placeholder="Enter Website URL (e.g., https://krishnapearls.com)" required>
                <button type="submit" id="submitBtn">
                    <span id="btnText">Run Deep Funnel Audit</span>
                    <div id="loader" class="loader hidden"></div>
                </button>
            </form>
            <div id="errorMessage" class="error-message hidden"></div>
        </section>

        <section id="resultsSection" class="results-section hidden">
            <div class="results-header">
                <h2>Deep Funnel Audit Report</h2>
                <div id="scannedUrls" class="scanned-urls"></div>
            </div>

            <div id="funnelSectionsContainer">
                <!-- Sections for Home, Category, Cart, Checkout injected here -->
            </div>
        </section>
    </main>

    <template id="funnelSectionTemplate">
        <div class="funnel-section">
            <h2 class="section-title"></h2>
            <p class="section-url"></p>
            <div class="insights-grid"></div>
            <div class="actionables-section">
                <h3>🔥 Top 10 Actionables for this Page</h3>
                <ol class="actionables-list"></ol>
            </div>
        </div>
    </template>

    <template id="insightCardTemplate">
        <div class="insight-card">
            <div class="card-header">
                <h3 class="element-title"></h3>
            </div>
            <img class="insight-image hidden" src="" alt="Element context">
            <div class="card-body">
                <div class="insight-row">
                    <span class="label current-label">Current</span>
                    <p class="value current-value"></p>
                </div>
                <div class="insight-row">
                    <span class="label gap-label">Gap</span>
                    <p class="value gap-value"></p>
                </div>
                <div class="insight-row fix-row">
                    <span class="label fix-label">Recommended Fix</span>
                    <p class="value fix-value"></p>
                </div>
            </div>
        </div>
    </template>

    <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js').then(reg => {
                    console.log('ServiceWorker registration successful');
                }).catch(err => {
                    console.log('ServiceWorker registration failed: ', err);
                });
            });
        }
    </script>
    <script>document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('auditForm');
    const homeUrlInput = document.getElementById('homeUrlInput');
    const submitBtn = document.getElementById('submitBtn');
    const btnText = document.getElementById('btnText');
    const loader = document.getElementById('loader');
    const errorMessage = document.getElementById('errorMessage');
    const resultsSection = document.getElementById('resultsSection');
    const scannedUrlsContainer = document.getElementById('scannedUrls');
    const funnelSectionsContainer = document.getElementById('funnelSectionsContainer');
    
    const funnelSectionTemplate = document.getElementById('funnelSectionTemplate');
    const insightCardTemplate = document.getElementById('insightCardTemplate');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const home_url = homeUrlInput.value.trim();
        if (!home_url) return;

        // Reset UI
        errorMessage.classList.add('hidden');
        resultsSection.classList.add('hidden');
        btnText.textContent = 'Auto-Crawling Funnel & Analyzing (takes 60s+)...';
        loader.classList.remove('hidden');
        submitBtn.disabled = true;
        funnelSectionsContainer.innerHTML = '';
        scannedUrlsContainer.innerHTML = '';

        try {
            const response = await fetch('/api/audit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ home_url })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Failed to analyze website.');
            }

            // Display top level target URL and competitors
            const urlsToDisplay = [
                { name: 'Target Target', url: data.target_url }
            ];
            if (data.competitors && data.competitors.length > 0) {
                data.competitors.forEach((c, i) => urlsToDisplay.push({ name: `Competitor ${i+1}`, url: c }));
            }

            urlsToDisplay.forEach(item => {
                const a = document.createElement('a');
                a.href = item.url;
                a.target = '_blank';
                a.className = 'scanned-url';
                a.textContent = `${item.name}: ${item.url}`;
                scannedUrlsContainer.appendChild(a);
            });

            // Render each funnel section
            const sections = ['Home', 'Category', 'Cart', 'Checkout'];
            
            sections.forEach(sectionName => {
                const sectionData = data.funnel_audit[sectionName];
                if (!sectionData) return;
                
                const sectionClone = funnelSectionTemplate.content.cloneNode(true);
                sectionClone.querySelector('.section-title').textContent = `${sectionName} Page Audit`;
                
                if (sectionData.url) {
                    sectionClone.querySelector('.section-url').textContent = sectionData.url;
                } else if (sectionData.error) {
                    sectionClone.querySelector('.section-url').textContent = `Could not access: ${sectionData.error}`;
                    sectionClone.querySelector('.section-url').style.color = '#ef4444';
                }

                // Render Insights
                const grid = sectionClone.querySelector('.insights-grid');
                if (sectionData.insights && Array.isArray(sectionData.insights)) {
                    sectionData.insights.forEach(insight => {
                        const cardClone = insightCardTemplate.content.cloneNode(true);
                        
                        cardClone.querySelector('.element-title').textContent = insight.element;
                        cardClone.querySelector('.current-value').textContent = insight.current;
                        cardClone.querySelector('.gap-value').textContent = insight.gap;
                        cardClone.querySelector('.fix-value').textContent = insight.fixes;
                        
                        if (insight.image_url) {
                            const img = cardClone.querySelector('.insight-image');
                            img.src = insight.image_url;
                            img.classList.remove('hidden');
                        }

                        if (insight.gap.includes('None')) {
                            cardClone.querySelector('.gap-label').style.color = '#10b981';
                            cardClone.querySelector('.fix-row').style.background = 'rgba(255, 255, 255, 0.05)';
                            cardClone.querySelector('.fix-row').style.borderColor = 'rgba(255, 255, 255, 0.1)';
                            cardClone.querySelector('.fix-label').style.color = 'var(--text-secondary)';
                        }

                        grid.appendChild(cardClone);
                    });
                }

                // Render Actionables
                const list = sectionClone.querySelector('.actionables-list');
                if (sectionData.actionables && Array.isArray(sectionData.actionables)) {
                    sectionData.actionables.forEach(actionable => {
                        const li = document.createElement('li');
                        const parts = actionable.split(':');
                        if (parts.length > 1) {
                            li.innerHTML = `<strong>${parts[0]}:</strong>${parts.slice(1).join(':')}`;
                        } else {
                            li.textContent = actionable;
                        }
                        list.appendChild(li);
                    });
                }

                funnelSectionsContainer.appendChild(sectionClone);
            });

            resultsSection.classList.remove('hidden');
            resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        } catch (error) {
            errorMessage.textContent = error.message;
            errorMessage.classList.remove('hidden');
        } finally {
            btnText.textContent = 'Run Deep Funnel Audit';
            loader.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });
});
</script>
</body>
</html>

"""

MANIFEST_JSON = """
{
  "name": "Website Auditor",
  "short_name": "Auditor",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0d0f12",
  "theme_color": "#0d0f12",
  "description": "Deep funnel analysis and competitor benchmarking.",
  "icons": [
    {
      "src": "icon-192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "icon-512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}

"""

SW_JS = """
const CACHE_NAME = 'auditor-cache-v1';
const urlsToCache = [
  '/',
  '/static/index.html',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll(urlsToCache);
      })
  );
});

self.addEventListener('fetch', event => {
  // Only cache GET requests, do not cache API POST requests
  if (event.request.method !== 'GET') return;
  
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        if (response) {
          return response;
        }
        return fetch(event.request);
      })
  );
});

"""

@app.route('/')
def serve_index():
    return render_template_string(INDEX_HTML)

@app.route('/manifest.json')
def serve_manifest():
    return Response(MANIFEST_JSON, mimetype='application/json')

@app.route('/sw.js')
def serve_sw():
    return Response(SW_JS, mimetype='application/javascript')
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
