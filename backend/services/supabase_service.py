"""
MatruRaksha AI - Database Service
Handles all Supabase database operations
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Initialize Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_KEY = SUPABASE_SERVICE_KEY or os.getenv("SUPABASE_KEY")

# Use service role key if available to bypass RLS for server-side inserts
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


async def get_mothers_by_telegram_id(telegram_chat_id: str) -> List[Dict[str, Any]]:
    """Return all mother profiles linked to a Telegram chat ID."""
    if not telegram_chat_id:
        return []

    try:
        response = (
            supabase.table("mothers")
            .select("*")
            .eq("telegram_chat_id", str(telegram_chat_id))
            .order("created_at", desc=True)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        logger.error(
            f"❌ Error fetching mothers by telegram_chat_id={telegram_chat_id}: {exc}",
            exc_info=True,
        )
        return []


async def get_mother_by_telegram_id(telegram_chat_id: str) -> Optional[Dict[str, Any]]:
    """Helper that returns the most recent mother profile for a chat ID."""
    mothers = await get_mothers_by_telegram_id(telegram_chat_id)
    return mothers[0] if mothers else None


async def get_recent_reports_for_mother(mother_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Return the latest parsed medical reports for a mother.
    The Telegram bot forwards these to the orchestrator for richer context.
    """
    if not mother_id:
        return []

    try:
        response = (
            supabase.table("medical_reports")
            .select("*")
            .eq("mother_id", str(mother_id))
            .order("uploaded_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        logger.error(
            f"❌ Error fetching medical reports for mother_id={mother_id}: {exc}",
            exc_info=True,
        )
        return []


class DatabaseService:
    """Service for database operations"""
    
    @staticmethod
    async def save_chat_history(
        mother_id: str,
        telegram_chat_id: str,
        user_message: str,
        agent_response: str,
        agent_type: str,
        response_time_ms: Optional[int] = None,
        intent_classification: Optional[str] = None,
        confidence_score: Optional[float] = None
    ) -> bool:
        """Save chat conversation to database"""
        try:
            data = {
                'mother_id': mother_id,
                'telegram_chat_id': telegram_chat_id,
                'user_message': user_message,
                'agent_response': agent_response,
                'agent_type': agent_type,
                'response_time_ms': response_time_ms,
                'intent_classification': intent_classification,
                'confidence_score': confidence_score,
                'message_timestamp': datetime.now().isoformat()
            }
            
            result = supabase.table('chat_histories').insert(data).execute()
            
            if result.data:
                logger.info(f"✅ Chat history saved for mother {mother_id}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"❌ Error saving chat history: {e}")
            return False
    
    @staticmethod
    def get_recent_chats(mother_id: str, limit: int = 10) -> List[Dict]:
        """Get recent chat history for a mother"""
        try:
            result = supabase.table('chat_histories').select('*').eq(
                'mother_id', mother_id
            ).order('message_timestamp', desc=True).limit(limit).execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"❌ Error fetching chat history: {e}")
            return []
    
    @staticmethod
    def get_upcoming_appointments(mother_id: str, days_ahead: int = 30) -> List[Dict]:
        """Get upcoming appointments for a mother"""
        try:
            future_date = (datetime.now() + timedelta(days=days_ahead)).isoformat()
            
            result = supabase.table('appointments').select('*').eq(
                'mother_id', mother_id
            ).gte('appointment_date', datetime.now().isoformat()).lte(
                'appointment_date', future_date
            ).eq('status', 'scheduled').order('appointment_date', desc=False).execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"❌ Error fetching appointments: {e}")
            return []
    
    @staticmethod
    def get_next_appointment(mother_id: str) -> Optional[Dict]:
        """Get the next upcoming appointment"""
        appointments = DatabaseService.get_upcoming_appointments(mother_id, days_ahead=60)
        return appointments[0] if appointments else None
    
    @staticmethod
    def create_appointment(
        mother_id: str,
        telegram_chat_id: str,
        appointment_type: str,
        appointment_date: datetime,
        appointment_location: Optional[str] = None,
        doctor_name: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Optional[Dict]:
        """Create a new appointment"""
        try:
            data = {
                'mother_id': mother_id,
                'telegram_chat_id': telegram_chat_id,
                'appointment_type': appointment_type,
                'appointment_date': appointment_date.isoformat(),
                'appointment_location': appointment_location,
                'doctor_name': doctor_name,
                'notes': notes,
                'status': 'scheduled'
            }
            
            result = supabase.table('appointments').insert(data).execute()
            
            if result.data:
                logger.info(f"✅ Appointment created for mother {mother_id}")
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"❌ Error creating appointment: {e}")
            return None
    
    @staticmethod
    def get_medical_reports(mother_id: str, limit: int = 5) -> List[Dict]:
        """Get recent medical reports"""
        try:
            result = supabase.table('medical_reports').select('*').eq(
                'mother_id', mother_id
            ).order('upload_date', desc=True).limit(limit).execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"❌ Error fetching medical reports: {e}")
            return []
    
    @staticmethod
    def get_mother_profile(mother_id: str) -> Optional[Dict]:
        """Get mother's complete profile"""
        try:
            result = supabase.table('mothers').select('*').eq('id', mother_id).execute()
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"❌ Error fetching mother profile: {e}")
            return None
    
    @staticmethod
    def save_health_metric(
        mother_id: str,
        weight_kg: Optional[float] = None,
        blood_pressure_systolic: Optional[int] = None,
        blood_pressure_diastolic: Optional[int] = None,
        hemoglobin: Optional[float] = None,
        blood_sugar: Optional[float] = None,
        notes: Optional[str] = None
    ) -> bool:
        """Save health metrics"""
        try:
            data = {
                'mother_id': mother_id,
                'weight_kg': weight_kg,
                'blood_pressure_systolic': blood_pressure_systolic,
                'blood_pressure_diastolic': blood_pressure_diastolic,
                'hemoglobin': hemoglobin,
                'blood_sugar': blood_sugar,
                'measured_at': datetime.now().isoformat(),
                'notes': notes
            }
            
            # Remove None values
            data = {k: v for k, v in data.items() if v is not None}
            
            result = supabase.table('health_metrics').insert(data).execute()
            
            if result.data:
                logger.info(f"✅ Health metrics saved for mother {mother_id}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"❌ Error saving health metrics: {e}")
            return False
    
    @staticmethod
    def get_health_metrics(mother_id: str, limit: int = 10) -> List[Dict]:
        """Get recent health metrics"""
        try:
            result = supabase.table('health_metrics').select('*').eq(
                'mother_id', mother_id
            ).order('measured_at', desc=True).limit(limit).execute()
            
            return result.data if result.data else []
            
        except Exception as e:
            logger.error(f"❌ Error fetching health metrics: {e}")
            return []
    
    @staticmethod
    def calculate_pregnancy_week(due_date: str) -> Optional[int]:
        """Calculate pregnancy week from due date"""
        try:
            due_date_obj = datetime.fromisoformat(due_date.replace('Z', '+00:00'))
            conception_date = due_date_obj - timedelta(weeks=40)
            weeks_pregnant = (datetime.now() - conception_date).days // 7
            return max(0, min(weeks_pregnant, 42))  # Cap at 0-42 weeks
            
        except Exception as e:
            logger.error(f"❌ Error calculating pregnancy week: {e}")
            return None
    
    @staticmethod
    def get_anc_schedule_status(mother_id: str) -> Dict[str, Any]:
        """Check ANC schedule compliance"""
        try:
            mother = DatabaseService.get_mother_profile(mother_id)
            if not mother:
                return {}
            
            pregnancy_week = DatabaseService.calculate_pregnancy_week(mother.get('due_date', ''))
            appointments = DatabaseService.get_upcoming_appointments(mother_id, days_ahead=365)
            completed_visits = len([a for a in appointments if a['status'] == 'completed'])
            
            # Minimum 4 ANC visits recommended
            recommended_visits = 4
            if pregnancy_week:
                if pregnancy_week >= 36:
                    recommended_visits = 8  # Weekly after 36 weeks
                elif pregnancy_week >= 28:
                    recommended_visits = 6  # Bi-weekly 28-36 weeks
            
            return {
                'pregnancy_week': pregnancy_week,
                'completed_visits': completed_visits,
                'recommended_visits': recommended_visits,
                'compliance': 'good' if completed_visits >= recommended_visits else 'needs_attention',
                'next_visit_due': 'now' if completed_visits < recommended_visits else 'on_track'
            }
            
        except Exception as e:
            logger.error(f"❌ Error checking ANC schedule: {e}")
            return {}


class SupabaseService:
    def __init__(self, client: Client):
        self.client = client

    # ---------------------- Appointments ----------------------
    def get_upcoming_appointments(self, mother_id: int) -> List[Dict[str, Any]]:
        """Return upcoming scheduled appointments for a mother."""
        now_iso = datetime.now().isoformat()
        resp = (
            self.client.table("appointments")
            .select("*")
            .eq("mother_id", mother_id)
            .eq("status", "scheduled")
            .gt("appointment_date", now_iso)
            .order("appointment_date", desc=False)
            .execute()
        )
        return resp.data or []

    def get_next_appointment(self, mother_id: int) -> Optional[Dict[str, Any]]:
        """Return the next upcoming appointment or the most recent past appointment as fallback."""
        upcoming = self.get_upcoming_appointments(mother_id)
        if upcoming:
            return upcoming[0]
        fallback = (
            self.client.table("appointments")
            .select("*")
            .eq("mother_id", mother_id)
            .order("appointment_date", desc=True)
            .limit(1)
            .execute()
        )
        return fallback.data[0] if fallback.data else None

    def create_appointment(
        self,
        mother_id: int,
        appointment_date: str,
        appointment_type: Optional[str] = None,
        status: str = "scheduled",
        notes: Optional[str] = None,
        facility: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new appointment.
        Aligns with Supabase `appointments` schema: appointment_date, appointment_type, status, notes, facility.
        """
        payload: Dict[str, Any] = {
            "mother_id": mother_id,
            "appointment_date": appointment_date,
            "status": status,
        }
        if appointment_type:
            payload["appointment_type"] = appointment_type
        if notes:
            payload["notes"] = notes
        if facility:
            payload["facility"] = facility

        resp = self.client.table("appointments").insert(payload).execute()
        return resp.data[0] if resp.data else {}

    # ---------------------- Medical Reports ----------------------
    def get_medical_reports(self, mother_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        resp = (
            self.client.table("medical_reports")
            .select("filename, analysis_summary, upload_date")
            .eq("mother_id", mother_id)
            .order("upload_date", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    # ---------------------- Mother Profile ----------------------
    def get_mother_profile(self, mother_id: int) -> Dict[str, Any]:
        resp = self.client.table("mothers").select("*").eq("id", mother_id).execute()
        return resp.data[0] if resp.data else {}

    # ---------------------- Health Metrics ----------------------
    def save_health_metric(self, mother_id: int, metric_type: str, value: Any, measured_at: Optional[str] = None) -> Dict[str, Any]:
        """Save a health metric entry. Schema assumes columns: mother_id, metric_type, value, measured_at."""
        payload = {
            "mother_id": mother_id,
            "metric_type": metric_type,
            "value": value,
            "measured_at": measured_at or datetime.now().isoformat(),
        }
        resp = self.client.table("health_metrics").insert(payload).execute()
        return resp.data[0] if resp.data else {}
