"""
GarageSaleMap Google Scraper
Aggregates garage/yard/estate sales from multiple sources via Google search.
Avoids direct site scraping - uses Google as aggregator.
"""
import time
import random
import logging
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
import re
from urllib.parse import quote_plus, urlparse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# User agents to rotate
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
]


@dataclass
class Sale:
    """Garage/yard/estate sale listing"""
    title: str
    url: str
    source: str  # facebook, craigslist, yardsalesearch, etc.
    address: Optional[str] = None
    date_text: Optional[str] = None
    description: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    scraped_at: str = None
    
    def __post_init__(self):
        if not self.scraped_at:
            self.scraped_at = datetime.utcnow().isoformat()


class GoogleSaleScraper:
    """Scrapes Google results for garage/yard/estate sales"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml',
        })
    
    def search_google(self, query: str, num_results: int = 20) -> List[dict]:
        """
        Search Google and extract results.
        Returns list of {title, url, snippet, source_domain}
        """
        results = []
        url = f"https://www.google.com/search?q={quote_plus(query)}&num={num_results}"
        
        headers = {
            'User-Agent': random.choice(USER_AGENTS)
        }
        
        try:
            time.sleep(random.uniform(2, 4))  # Be polite
            response = self.session.get(url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                log.warning(f"Google returned {response.status_code}")
                return results
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find search result divs
            search_divs = soup.find_all('div', class_='g')
            
            for div in search_divs:
                try:
                    # Extract title
                    title_tag = div.find('h3')
                    if not title_tag:
                        continue
                    title = title_tag.get_text(strip=True)
                    
                    # Extract URL
                    link_tag = div.find('a')
                    if not link_tag or 'href' not in link_tag.attrs:
                        continue
                    url = link_tag['href']
                    
                    # Extract snippet
                    snippet_divs = div.find_all('div', class_=['VwiC3b', 'lyLwlc'])
                    snippet = ""
                    for s in snippet_divs:
                        snippet += s.get_text(strip=True) + " "
                    snippet = snippet.strip()
                    
                    # Determine source
                    domain = urlparse(url).netloc.lower()
                    source = self._identify_source(domain)
                    
                    results.append({
                        'title': title,
                        'url': url,
                        'snippet': snippet,
                        'source': source,
                        'domain': domain,
                    })
                    
                except Exception as e:
                    log.debug(f"Error parsing result: {e}")
                    continue
            
            log.info(f"Found {len(results)} results for: {query}")
            return results
            
        except Exception as e:
            log.error(f"Google search failed: {e}")
            return results
    
    def _identify_source(self, domain: str) -> str:
        """Identify source platform from domain"""
        if 'facebook' in domain:
            return 'Facebook'
        elif 'craigslist' in domain:
            return 'Craigslist'
        elif 'yardsalesearch' in domain:
            return 'YardSaleSearch'
        elif 'estatesales.net' in domain:
            return 'EstateSales.net'
        elif 'gsalr.com' in domain:
            return 'Gsalr'
        elif 'nextdoor' in domain:
            return 'Nextdoor'
        else:
            return domain.replace('www.', '').split('.')[0].title()
    
    def extract_address_from_snippet(self, snippet: str) -> Optional[str]:
        """
        Try to extract address from Google snippet.
        Looks for patterns like: "123 Main St, City, ST 12345"
        """
        # Common address patterns
        patterns = [
            r'\d+\s+[\w\s]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct|Circle|Cir|Place|Pl)[,\s]+[\w\s]+[,\s]+[A-Z]{2}\s+\d{5}',
            r'\d+\s+[\w\s]+,\s+[\w\s]+,\s+[A-Z]{2}\s+\d{5}',
            r'\d+\s+[\w\s]+,\s+[\w\s]+,\s+[A-Z]{2}',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, snippet)
            if match:
                return match.group(0).strip()
        
        return None
    
    def extract_date_from_text(self, text: str) -> Optional[str]:
        """Extract date mentions from text"""
        # Look for date patterns
        date_patterns = [
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s+\d{4})?',
            r'\d{1,2}/\d{1,2}(?:/\d{2,4})?',
            r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)',
        ]
        
        dates = []
        for pattern in date_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            dates.extend(matches)
        
        return ", ".join(dates) if dates else None
    
    def scrape_location(self, location: str, radius_miles: int = 25) -> List[Sale]:
        """
        Scrape garage sales near a location.
        
        Args:
            location: City, zip, or address
            radius_miles: Search radius
            
        Returns:
            List of Sale objects
        """
        all_sales = []
        seen_urls = set()
        
        # Build search queries for different sources
        queries = [
            f'"garage sale" OR "yard sale" {location} site:facebook.com/marketplace',
            f'"garage sale" OR "yard sale" {location} site:craigslist.org',
            f'"estate sale" {location} site:estatesales.net',
            f'"yard sale" {location} site:yardsalesearch.com',
            f'"garage sale" {location} site:gsalr.com',
        ]
        
        for query in queries:
            log.info(f"Searching: {query}")
            results = self.search_google(query, num_results=10)
            
            for result in results:
                url = result['url']
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # Extract data from snippet
                address = self.extract_address_from_snippet(result['snippet'])
                date_text = self.extract_date_from_text(result['snippet'])
                
                sale = Sale(
                    title=result['title'],
                    url=url,
                    source=result['source'],
                    address=address,
                    date_text=date_text,
                    description=result['snippet'][:400],
                )
                
                all_sales.append(sale)
                log.info(f"  + [{sale.source}] {sale.title[:50]}")
            
            # Rate limiting
            time.sleep(random.uniform(3, 6))
        
        log.info(f"Total sales found: {len(all_sales)}")
        return all_sales
    
    def geocode_address(self, address: str) -> tuple[Optional[float], Optional[float]]:
        """
        Geocode address to lat/lon using free OpenStreetMap Nominatim.
        
        Returns:
            (latitude, longitude) or (None, None)
        """
        if not address:
            return None, None
        
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': address,
            'format': 'json',
            'limit': 1,
        }
        headers = {
            'User-Agent': 'GarageSaleMap/1.0'
        }
        
        try:
            time.sleep(1)  # Nominatim requires 1 req/sec max
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    lat = float(data[0]['lat'])
                    lon = float(data[0]['lon'])
                    log.info(f"Geocoded: {address} -> ({lat}, {lon})")
                    return lat, lon
        
        except Exception as e:
            log.debug(f"Geocoding failed for {address}: {e}")
        
        return None, None


# CLI test
if __name__ == "__main__":
    scraper = GoogleSaleScraper()
    
    # Test search
    sales = scraper.scrape_location("Fort Myers, FL", radius_miles=25)
    
    print(f"\n{'='*80}")
    print(f"Found {len(sales)} sales:")
    print(f"{'='*80}\n")
    
    for sale in sales:
        print(f"[{sale.source}] {sale.title}")
        print(f"  URL: {sale.url}")
        if sale.address:
            print(f"  Address: {sale.address}")
        if sale.date_text:
            print(f"  Date: {sale.date_text}")
        print()
