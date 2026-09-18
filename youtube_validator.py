"""
TitleForge YouTube Official Channel Filter
Filters YouTube channels to return only official/verified channels
for Movies, TV Shows, and Video Games
"""

import re
import requests
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs


class YouTubeChannelValidator:
    """
    Validates and filters YouTube channels to identify official ones
    Uses multiple signals: verification, naming patterns, official source APIs
    """
    
    # Keywords indicating official status
    OFFICIAL_INDICATORS = {
        'official': 10,
        'verified': 10,
        'official channel': 15,
        'official page': 10,
        'official youtube': 15,
        'studio': 8,
        'production': 8,
        'entertainment': 5,
    }
    
    # Keywords indicating unofficial/fan status (disqualifying)
    UNOFFICIAL_KEYWORDS = {
        'fan': True,
        'tribute': True,
        'unofficial': True,
        'not official': True,
        'fanpage': True,
        'fan channel': True,
        'fan page': True,
        'fan created': True,
        'fan made': True,
        'covers': True,
        'clips': True,
        'highlights': True,
        'reaction': True,
        'remix': True,
        'parody': True,
        'trailer park': True,
        'best scenes': True,
        'full movie': True,
        'movie clips': True,
    }
    
    def __init__(self, use_wikidata: bool = True, use_imdb: bool = True):
        """
        Initialize the validator
        
        Args:
            use_wikidata: Use Wikidata official YouTube channel info
            use_imdb: Use IMDb official YouTube channel info
        """
        self.use_wikidata = use_wikidata
        self.use_imdb = use_imdb
    
    def extract_channel_id(self, youtube_url: str) -> Optional[str]:
        """Extract YouTube channel ID from various URL formats"""
        if not youtube_url:
            return None
        
        patterns = [
            r'youtube\.com/channel/([A-Za-z0-9_-]+)',
            r'youtube\.com/@([A-Za-z0-9_-]+)',
            r'youtube\.com/c/([A-Za-z0-9_-]+)',
            r'youtube\.com/user/([A-Za-z0-9_-]+)',
            r'youtu\.be/([A-Za-z0-9_-]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, youtube_url)
            if match:
                return match.group(1)
        
        return None
    
    def get_channel_name_from_url(self, youtube_url: str) -> Optional[str]:
        """Extract channel name/handle from YouTube URL"""
        if not youtube_url:
            return None
        
        # Try to get from @handle
        match = re.search(r'youtube\.com/@([^/?]+)', youtube_url)
        if match:
            return match.group(1)
        
        # Try to get from /c/ path
        match = re.search(r'youtube\.com/c/([^/?]+)', youtube_url)
        if match:
            return match.group(1)
        
        # Try to get from /user/ path
        match = re.search(r'youtube\.com/user/([^/?]+)', youtube_url)
        if match:
            return match.group(1)
        
        return None
    
    def score_channel(self, channel_data: Dict) -> Tuple[int, str]:
        """
        Score a channel for likelihood of being official
        
        Args:
            channel_data: Dict with 'title', 'description', 'is_verified', etc.
        
        Returns:
            Tuple of (score, reason)
        """
        title = (channel_data.get('title') or '').lower()
        description = (channel_data.get('description') or '').lower()
        is_verified = channel_data.get('is_verified', False)
        subscriber_count = channel_data.get('subscriber_count', 0)
        
        score = 0
        reason = []
        
        # Check for disqualifying keywords first
        for keyword in self.UNOFFICIAL_KEYWORDS:
            if keyword in title or keyword in description:
                return (0, f"Contains unofficial keyword: '{keyword}'")
        
        # YouTube verification badge - strong signal
        if is_verified:
            score += 100
            reason.append("YouTube verified")
        
        # Check for official indicators in title
        for keyword, weight in self.OFFICIAL_INDICATORS.items():
            if keyword in title:
                score += weight
                reason.append(f"Official indicator in title: '{keyword}'")
                break  # Only count the highest weight match
        
        # Check for official indicators in description
        for keyword, weight in self.OFFICIAL_INDICATORS.items():
            if keyword in description:
                score += int(weight * 0.7)  # Description counts slightly less
                reason.append(f"Official indicator in description: '{keyword}'")
                break
        
        # Higher subscriber count for official channels (weak signal)
        if subscriber_count > 100000:
            score += 10
            reason.append("Large subscriber base")
        
        return (score, '; '.join(reason) if reason else 'Standard channel')
    
    def is_official(self, channel_data: Dict, threshold: int = 15) -> bool:
        """
        Determine if a channel is official
        
        Args:
            channel_data: Channel information
            threshold: Minimum score to be considered official (default: 15)
        
        Returns:
            bool: True if channel is likely official
        """
        score, _ = self.score_channel(channel_data)
        return score >= threshold
    
    def filter_channels(
        self,
        channels: List[Dict],
        official_only: bool = True,
        threshold: int = 15
    ) -> List[Dict]:
        """
        Filter and rank YouTube channels
        
        Args:
            channels: List of channel data
            official_only: If True, return only official channels
            threshold: Score threshold for official classification
        
        Returns:
            Filtered and ranked list of channels
        """
        if not channels:
            return []
        
        # Score all channels
        scored = []
        for channel in channels:
            score, reason = self.score_channel(channel)
            is_official = score >= threshold
            
            if official_only and not is_official:
                continue
            
            scored.append({
                **channel,
                'official_score': score,
                'official_reason': reason,
                'is_official': is_official
            })
        
        # Sort by score (descending), then by subscriber count
        scored.sort(
            key=lambda x: (
                -x['official_score'],
                -x.get('subscriber_count', 0)
            )
        )
        
        return scored
    
    def get_wikidata_official_youtube(self, wikidata_id: str) -> Optional[str]:
        """
        Get official YouTube channel from Wikidata
        Property P2397 = YouTube channel ID
        """
        try:
            url = f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            entity = data.get('entities', {}).get(wikidata_id, {})
            claims = entity.get('claims', {})
            
            # P2397 is YouTube channel ID property
            if 'P2397' in claims:
                channel_id = claims['P2397'][0]['mainsnak']['datavalue']['value']
                return f"https://www.youtube.com/channel/{channel_id}"
        except Exception as e:
            print(f"Error fetching Wikidata: {e}")
        
        return None
    
    def validate_against_official_source(
        self,
        youtube_url: str,
        wikidata_id: Optional[str] = None
    ) -> bool:
        """
        Validate YouTube channel against official sources
        
        Args:
            youtube_url: YouTube channel URL
            wikidata_id: Wikidata ID for entity
        
        Returns:
            bool: True if matches official source or no source available
        """
        if not self.use_wikidata or not wikidata_id:
            return True
        
        official_url = self.get_wikidata_official_youtube(wikidata_id)
        if not official_url:
            return True  # Can't validate, so pass
        
        # Compare channel IDs
        input_channel_id = self.extract_channel_id(youtube_url)
        official_channel_id = self.extract_channel_id(official_url)
        
        return input_channel_id == official_channel_id if input_channel_id else True


class YouTubeResultProcessor:
    """
    Process YouTube search results and filter for official channels
    Integrates with TitleForge's existing YouTube search logic
    """
    
    def __init__(self):
        self.validator = YouTubeChannelValidator()
    
    def process_youtube_results(
        self,
        youtube_results: List[Dict],
        title: str,
        wikidata_id: Optional[str] = None,
        official_only: bool = True
    ) -> List[str]:
        """
        Process YouTube search results and return official channel URLs
        
        Args:
            youtube_results: List of YouTube channel results
            title: The title being processed (for context)
            wikidata_id: Optional Wikidata ID for official source verification
            official_only: Return only official channels
        
        Returns:
            List of YouTube channel URLs
        """
        # Filter to official channels
        filtered = self.validator.filter_channels(
            youtube_results,
            official_only=official_only
        )
        
        # If we have Wikidata ID, validate against it
        if wikidata_id:
            official_url = self.validator.get_wikidata_official_youtube(wikidata_id)
            if official_url:
                # Put the official source channel first
                return [official_url] + [
                    ch['url'] for ch in filtered 
                    if ch['url'] != official_url
                ][:4]  # Limit to 5 total (1 official + 4 others)
        
        # Return URLs from filtered results
        return [ch['url'] for ch in filtered][:5]


# Integration function for Flask backend
def apply_youtube_filter_to_response(response_data: Dict, official_only: bool = True) -> Dict:
    """
    Apply YouTube filtering to a response from the TitleForge API
    
    Args:
        response_data: The full response dict containing youtube_channel_username
        official_only: Whether to keep only official channels
    
    Returns:
        Modified response with filtered YouTube channels
    """
    processor = YouTubeResultProcessor()
    
    # Filter the youtube_channel_username field
    if 'youtube_channel_username' in response_data:
        original_urls = response_data['youtube_channel_username']
        if isinstance(original_urls, str):
            # Single URL - already official (or none)
            pass
        elif isinstance(original_urls, list):
            # Multiple URLs - filter them
            channels = [
                {'url': url, 'title': '', 'description': '', 'is_verified': False}
                for url in original_urls
            ]
            filtered_urls = processor.process_youtube_results(
                channels,
                response_data.get('title', ''),
                official_only=official_only
            )
            response_data['youtube_channel_username'] = filtered_urls
    
    return response_data
