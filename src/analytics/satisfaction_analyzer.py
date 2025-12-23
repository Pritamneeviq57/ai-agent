"""
Customer Satisfaction Monitor and Concern Pattern Identifier
Analyzes meeting transcripts to identify satisfaction levels and concern patterns.
"""
import re
from typing import Dict, List, Tuple, Optional
from collections import Counter, defaultdict
from datetime import datetime
import json

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False
    print("Warning: TextBlob not available. Using keyword-based analysis only.")


class SatisfactionAnalyzer:
    """Analyze customer satisfaction and identify concern patterns from transcripts"""
    
    # Satisfaction indicators (positive)
    SATISFACTION_KEYWORDS = {
        'positive': [
            'excellent', 'great', 'good', 'perfect', 'amazing', 'wonderful',
            'satisfied', 'happy', 'pleased', 'impressed', 'love', 'appreciate',
            'thank you', 'thanks', 'grateful', 'helpful', 'useful', 'valuable',
            'exactly what', 'meets expectations', 'exceeded', 'beyond',
            'recommend', 'would use again', 'definitely', 'absolutely'
        ],
        'negative': [
            'disappointed', 'frustrated', 'unhappy', 'unsatisfied', 'poor',
            'terrible', 'awful', 'bad', 'worst', 'hate', 'dislike',
            'not working', 'broken', 'issue', 'problem', 'concern', 'worry',
            'not good enough', 'doesn\'t meet', 'below expectations',
            'waste of time', 'not helpful', 'useless', 'ineffective'
        ],
        'concern': [
            'concern', 'worried', 'worry', 'anxious', 'nervous', 'uncertain',
            'not sure', 'question', 'doubt', 'hesitant', 'apprehensive',
            'risk', 'risky', 'uncertainty', 'unclear', 'confused', 'confusion'
        ],
        'issue': [
            'bug', 'error', 'broken', 'not working', 'failed', 'failure',
            'problem', 'issue', 'glitch', 'malfunction', 'defect', 'flaw',
            'slow', 'lag', 'timeout', 'crash', 'down', 'outage'
        ],
        'escalation': [
            'escalate', 'manager', 'supervisor', 'complaint', 'complain',
            'refund', 'cancel', 'terminate', 'switch', 'competitor',
            'legal', 'lawyer', 'sue', 'lawsuit', 'breach', 'violation'
        ],
        'urgency': [
            'urgent', 'asap', 'immediately', 'critical', 'important',
            'priority', 'emergency', 'now', 'right away', 'soon',
            'deadline', 'time sensitive', 'cannot wait'
        ]
    }
    
    # Concern categories
    CONCERN_CATEGORIES = {
        'technical': ['bug', 'error', 'broken', 'not working', 'slow', 'crash', 'glitch', 'technical issue'],
        'performance': ['slow', 'lag', 'performance', 'speed', 'timeout', 'bottleneck', 'optimization'],
        'feature': ['missing', 'need', 'want', 'should have', 'would like', 'feature request', 'enhancement'],
        'support': ['support', 'help', 'assistance', 'response time', 'waiting', 'unresponsive'],
        'pricing': ['expensive', 'cost', 'price', 'pricing', 'budget', 'afford', 'value', 'worth'],
        'reliability': ['unreliable', 'inconsistent', 'unstable', 'downtime', 'outage', 'unavailable'],
        'usability': ['difficult', 'complex', 'confusing', 'hard to use', 'user friendly', 'intuitive'],
        'security': ['security', 'privacy', 'data', 'breach', 'safe', 'secure', 'protection']
    }
    
    def __init__(self):
        """Initialize the analyzer"""
        self.sentiment_cache = {}
    
    def analyze_transcript(self, transcript_text: str, chat_text: Optional[str] = None) -> Dict:
        """
        Analyze transcript for satisfaction and concerns
        
        Args:
            transcript_text: Main transcript text
            chat_text: Optional chat messages
            
        Returns:
            Dictionary with satisfaction metrics and concerns
        """
        if not transcript_text:
            return self._empty_result()
        
        # Combine transcript and chat
        full_text = transcript_text.lower()
        if chat_text:
            full_text += " " + chat_text.lower()
        
        # Calculate satisfaction score
        satisfaction_score = self._calculate_satisfaction_score(full_text)
        
        # Identify concerns
        concerns = self._identify_concerns(full_text)
        
        # Categorize concerns
        concern_categories = self._categorize_concerns(concerns, full_text)
        
        # Calculate sentiment
        sentiment = self._calculate_sentiment(transcript_text)
        
        # Extract key phrases
        key_phrases = self._extract_key_phrases(full_text, concerns)
        
        # Calculate urgency level
        urgency_level = self._calculate_urgency(full_text)
        
        # Overall risk score
        risk_score = self._calculate_risk_score(satisfaction_score, concerns, urgency_level)
        
        return {
            'satisfaction_score': satisfaction_score,
            'sentiment': sentiment,
            'concerns': concerns,
            'concern_categories': concern_categories,
            'key_phrases': key_phrases,
            'urgency_level': urgency_level,
            'risk_score': risk_score,
            'analyzed_at': datetime.now().isoformat(),
            'transcript_length': len(transcript_text),
            'has_chat': bool(chat_text)
        }
    
    def _calculate_satisfaction_score(self, text: str) -> float:
        """Calculate satisfaction score from 0-100"""
        positive_count = sum(1 for keyword in self.SATISFACTION_KEYWORDS['positive'] 
                            if keyword in text)
        negative_count = sum(1 for keyword in self.SATISFACTION_KEYWORDS['negative'] 
                            if keyword in text)
        
        total_keywords = positive_count + negative_count
        if total_keywords == 0:
            return 50.0  # Neutral
        
        # Score: 0-100 where 100 is most satisfied
        score = (positive_count / total_keywords) * 100
        
        # Adjust based on sentiment if available
        if TEXTBLOB_AVAILABLE:
            try:
                blob = TextBlob(text[:5000])  # Limit for performance
                polarity = blob.sentiment.polarity  # -1 to 1
                sentiment_adjustment = (polarity + 1) * 50  # Convert to 0-100
                score = (score * 0.7) + (sentiment_adjustment * 0.3)  # Weighted average
            except:
                pass
        
        return round(max(0, min(100, score)), 2)
    
    def _identify_concerns(self, text: str) -> List[Dict]:
        """Identify specific concerns mentioned in the text"""
        concerns = []
        
        # Split text into sentences to get full context
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        # Check for concern keywords
        for concern_type, keywords in self.SATISFACTION_KEYWORDS.items():
            if concern_type in ['concern', 'issue', 'escalation']:
                for keyword in keywords:
                    if keyword in text:
                        # Find sentences containing the keyword
                        for sentence in sentences:
                            if keyword in sentence.lower():
                                # Clean the context - remove timestamps and XML tags
                                context = self._clean_vtt_text(sentence.strip())
                                if context:  # Only add non-empty contexts
                                    concerns.append({
                                        'type': concern_type,
                                        'keyword': keyword,
                                        'context': context,
                                        'severity': self._get_severity(concern_type, keyword)
                                    })
        
        # Remove duplicates and sort by severity
        unique_concerns = []
        seen_contexts = set()
        for concern in concerns:
            context_key = concern['context'][:100]  # Use first 100 chars as key to avoid duplicates
            if context_key not in seen_contexts:
                seen_contexts.add(context_key)
                unique_concerns.append(concern)
        
        # Sort by severity (highest first)
        unique_concerns.sort(key=lambda x: x['severity'], reverse=True)
        
        return unique_concerns[:10]  # Top 10 concerns
    
    def _categorize_concerns(self, concerns: List[Dict], text: str) -> Dict[str, int]:
        """Categorize concerns by type"""
        category_counts = defaultdict(int)
        
        for concern in concerns:
            keyword = concern['keyword'].lower()
            for category, keywords in self.CONCERN_CATEGORIES.items():
                if any(cat_keyword in keyword or cat_keyword in text.lower() 
                      for cat_keyword in keywords):
                    category_counts[category] += 1
                    break
        
        return dict(category_counts)
    
    def _extract_key_phrases(self, text: str, concerns: List[Dict]) -> List[str]:
        """Extract key phrases related to concerns"""
        key_phrases = []
        
        # Extract sentences with concern keywords
        sentences = re.split(r'[.!?]\s+', text)
        for sentence in sentences:
            sentence_lower = sentence.lower()
            if any(concern['keyword'] in sentence_lower for concern in concerns[:5]):
                # Clean up the sentence
                cleaned = sentence.strip()[:200]  # Limit length
                if len(cleaned) > 20:  # Only meaningful phrases
                    key_phrases.append(cleaned)
        
        return key_phrases[:5]  # Top 5 phrases
    
    def _calculate_sentiment(self, text: str) -> Dict[str, float]:
        """Calculate sentiment scores with reason"""
        text_lower = text.lower()
        
        # Count positive and negative keywords
        positive_keywords = [kw for kw in self.SATISFACTION_KEYWORDS['positive'] if kw in text_lower]
        negative_keywords = [kw for kw in self.SATISFACTION_KEYWORDS['negative'] if kw in text_lower]
        
        positive = len(positive_keywords)
        negative = len(negative_keywords)
        total = positive + negative
        
        # Calculate polarity using TextBlob if available
        if TEXTBLOB_AVAILABLE:
            try:
                blob = TextBlob(text[:5000])  # Limit for performance
                polarity = round(blob.sentiment.polarity, 3)
                subjectivity = round(blob.sentiment.subjectivity, 3)
            except Exception as e:
                polarity = (positive - negative) / max(total, 1) if total > 0 else 0.0
                subjectivity = 0.5
        else:
            polarity = (positive - negative) / max(total, 1) if total > 0 else 0.0
            subjectivity = 0.5
        
        # Generate sentiment reason
        sentiment_reason = self._generate_sentiment_reason(polarity, positive, negative, 
                                                           positive_keywords, negative_keywords, text_lower)
        
        return {
            'polarity': round(polarity, 3),
            'subjectivity': round(subjectivity, 3),
            'reason': sentiment_reason,
            'positive_count': positive,
            'negative_count': negative
        }
    
    def _generate_sentiment_reason(self, polarity: float, positive_count: int, 
                                   negative_count: int, positive_keywords: List[str], 
                                   negative_keywords: List[str], text: str) -> str:
        """Generate human-readable reason for sentiment"""
        if polarity > 0.3:
            # Positive sentiment
            reason_parts = []
            reason_parts.append(f"**Positive Sentiment Detected** ({positive_count} positive indicators)")
            
            if positive_keywords:
                top_keywords = positive_keywords[:8]
                keywords_text = '\n• '.join(top_keywords)
                reason_parts.append(f"\n\n**Positive words used by client:**\n• {keywords_text}")
            
            # Check for specific positive behaviors with actual phrases
            positive_behaviors = []
            if any(kw in text for kw in ['thank', 'thanks', 'grateful', 'appreciate']):
                positive_behaviors.append("Showed appreciation and gratitude")
            if any(kw in text for kw in ['excellent', 'great', 'perfect', 'amazing']):
                positive_behaviors.append("Used strong positive language")
            if any(kw in text for kw in ['satisfied', 'happy', 'pleased']):
                positive_behaviors.append("Explicitly stated satisfaction")
            
            if positive_behaviors:
                behaviors_text = '\n• '.join(positive_behaviors)
                reason_parts.append(f"\n\n**Client behavior:**\n• {behaviors_text}")
            
            return "\n".join(reason_parts)
        
        elif polarity < -0.1:
            # Negative sentiment
            reason_parts = []
            reason_parts.append(f"**Negative Sentiment Detected** ({negative_count} concern indicators)")
            
            if negative_keywords:
                top_keywords = negative_keywords[:8]
                keywords_text = '\n• '.join(top_keywords)
                reason_parts.append(f"\n\n**Negative/Concern words used by client:**\n• {keywords_text}")
            
            # Check for specific negative behaviors
            negative_behaviors = []
            if any(kw in text for kw in ['frustrated', 'disappointed', 'unhappy']):
                negative_behaviors.append("Expressed frustration or disappointment")
            if any(kw in text for kw in ['problem', 'issue', 'broken', 'not working']):
                negative_behaviors.append("Reported technical or operational issues")
            if any(kw in text for kw in ['complaint', 'complain', 'escalate', 'terminate', 'cancel']):
                negative_behaviors.append("Raised complaints or escalation concerns")
            
            if negative_behaviors:
                behaviors_text = '\n• '.join(negative_behaviors)
                reason_parts.append(f"\n\n**Client behavior:**\n• {behaviors_text}")
            
            return "\n".join(reason_parts)
        
        else:
            # Neutral/Mixed sentiment
            reason_parts = []
            if positive_count > 0 and negative_count > 0:
                reason_parts.append(f"**Mixed Sentiment** (Positive: {positive_count}, Negative: {negative_count})")
                
                if positive_keywords:
                    top_pos = positive_keywords[:5]
                    reason_parts.append(f"\n\n**Positive words:** {', '.join(top_pos)}")
                
                if negative_keywords:
                    top_neg = negative_keywords[:5]
                    reason_parts.append(f"\n\n**Concern words:** {', '.join(top_neg)}")
                
                reason_parts.append("\n\n**Analysis:** Meeting had balanced discussion with both concerns and positive feedback.")
            
            elif positive_count == 0 and negative_count == 0:
                reason_parts.append("**Neutral Sentiment**\n\nFactual, business-focused discussion. No strong emotional indicators detected.")
            else:
                reason_parts.append("**Neutral Sentiment**\n\nProfessional tone maintained. Client focused on business matters.")
            
            return "\n".join(reason_parts)
    
    def _calculate_urgency(self, text: str) -> str:
        """Determine urgency level"""
        urgency_count = sum(1 for keyword in self.SATISFACTION_KEYWORDS['urgency'] 
                           if keyword in text)
        escalation_count = sum(1 for keyword in self.SATISFACTION_KEYWORDS['escalation'] 
                              if keyword in text)
        
        if escalation_count > 0 or urgency_count > 3:
            return 'high'
        elif urgency_count > 1:
            return 'medium'
        elif urgency_count > 0:
            return 'low'
        else:
            return 'none'
    
    def _calculate_risk_score(self, satisfaction: float, concerns: List[Dict], 
                              urgency: str) -> float:
        """Calculate overall risk score (0-100, higher = more risk)"""
        # Base risk from satisfaction (low satisfaction = high risk)
        risk = 100 - satisfaction
        
        # Add risk from concerns
        concern_risk = len(concerns) * 5  # 5 points per concern
        
        # Add risk from urgency
        urgency_multiplier = {
            'high': 1.5,
            'medium': 1.2,
            'low': 1.1,
            'none': 1.0
        }
        
        risk = (risk + concern_risk) * urgency_multiplier.get(urgency, 1.0)
        
        return round(min(100, max(0, risk)), 2)
    
    def _clean_vtt_text(self, text: str) -> str:
        """Clean VTT timestamps and XML tags from text, but keep speaker names"""
        if not text:
            return ""
        
        # Remove VTT timestamps (e.g., "00:23:03.918 --> 00:23:04.918")
        text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}', '', text)
        
        # Extract speaker name from XML tags and reformat
        # Pattern: <v person name>text</v> -> "Person Name: text"
        def replace_speaker_tag(match):
            speaker = match.group(1).strip()
            content = match.group(2).strip()
            # Capitalize speaker name properly
            speaker_formatted = ' '.join(word.capitalize() for word in speaker.split())
            return f"{speaker_formatted}: {content}"
        
        text = re.sub(r'<v\s+([^>]+)>([^<]+)</v>', replace_speaker_tag, text, flags=re.IGNORECASE)
        
        # Remove any remaining XML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def _get_severity(self, concern_type: str, keyword: str) -> int:
        """Get severity level (1-5)"""
        severity_map = {
            'escalation': 5,
            'issue': 4,
            'concern': 3,
            'negative': 2
        }
        return severity_map.get(concern_type, 2)
    
    def _empty_result(self) -> Dict:
        """Return empty result structure"""
        return {
            'satisfaction_score': 50.0,
            'sentiment': {'polarity': 0.0, 'subjectivity': 0.5},
            'concerns': [],
            'concern_categories': {},
            'key_phrases': [],
            'urgency_level': 'none',
            'risk_score': 50.0,
            'analyzed_at': datetime.now().isoformat(),
            'transcript_length': 0,
            'has_chat': False
        }
    
    def get_satisfaction_label(self, score: float) -> Tuple[str, str]:
        """Get satisfaction label and color"""
        if score >= 75:
            return ('Excellent', '🟢')
        elif score >= 60:
            return ('Good', '🟡')
        elif score >= 40:
            return ('Fair', '🟠')
        else:
            return ('Poor', '🔴')
    
    def get_risk_label(self, score: float) -> Tuple[str, str]:
        """Get risk label and color"""
        if score >= 70:
            return ('High Risk', '🔴')
        elif score >= 40:
            return ('Medium Risk', '🟠')
        elif score >= 20:
            return ('Low Risk', '🟡')
        else:
            return ('Minimal Risk', '🟢')

