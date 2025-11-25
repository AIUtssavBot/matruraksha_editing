import os
import logging
import requests
import threading
import time
import asyncio
import base64
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, status, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Initialize router
router = APIRouter()
logger = logging.getLogger(__name__)

# ==================== GLOBAL VARIABLES ====================
telegram_bot_app = None
bot_thread = None
bot_running = False

# ==================== ENVIRONMENT VALIDATION ====================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.warning("⚠️  Supabase credentials not found in .env")
    SUPABASE_URL = "https://placeholder.supabase.co"
    SUPABASE_KEY = "placeholder"

if not TELEGRAM_BOT_TOKEN:
    logger.warning("⚠️  TELEGRAM_BOT_TOKEN not found in .env")
    TELEGRAM_BOT_TOKEN = "placeholder"

if not GEMINI_API_KEY:
    logger.warning("⚠️  GEMINI_API_KEY not found in .env")
    GEMINI_API_KEY = None

try:
    effective_key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY
    supabase: Client = create_client(SUPABASE_URL, effective_key)
    logger.info("✅ Supabase client initialized")
except Exception as e:
    logger.error(f"❌ Supabase initialization error: {e}")
    supabase = None

# ==================== GEMINI AI INITIALIZATION ====================
try:
    import google.generativeai as genai
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
        logger.info("✅ Gemini AI initialized")
    else:
        GEMINI_AVAILABLE = False
        logger.warning("⚠️  Gemini API key not set")
except ImportError as e:
    logger.warning(f"⚠️  Gemini not available: {e}")
    logger.warning("⚠️  Install with: pip install google-generativeai")
    GEMINI_AVAILABLE = False

# ==================== AI AGENTS IMPORT ====================
try:
    from agents.orchestrator import orchestrator
    AGENTS_AVAILABLE = True
    logger.info("✅ AI Agents loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️  AI Agents not available: {e}")
    logger.warning("⚠️  System will work without AI agents")
    AGENTS_AVAILABLE = False
    orchestrator = None

# ==================== PYDANTIC MODELS ====================
class Mother(BaseModel):
    name: str
    phone: str
    age: int
    gravida: int
    parity: int
    bmi: float
    location: str
    preferred_language: str = "en"
    telegram_chat_id: Optional[str] = None
    due_date: Optional[str] = None

class RiskAssessment(BaseModel):
    mother_id: str
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    heart_rate: Optional[int] = None
    blood_glucose: Optional[float] = None
    hemoglobin: Optional[float] = None
    proteinuria: int = 0
    edema: int = 0
    headache: int = 0
    vision_changes: int = 0
    epigastric_pain: int = 0
    vaginal_bleeding: int = 0
    notes: Optional[str] = None

class DocumentAnalysisRequest(BaseModel):
    report_id: str  # UUID as string
    mother_id: str  # UUID as string
    file_url: str
    file_type: str

class AgentQuery(BaseModel):
    mother_id: str
    query: str
    context: Optional[Dict] = None

class DailyCheckIn(BaseModel):
    mother_id: str
    date: str
    weight: Optional[float] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    symptoms: Optional[List[str]] = []
    medications_taken: bool = True
    feeling_today: str = "good"
    notes: Optional[str] = None

# ==================== TELEGRAM BOT FUNCTIONS ====================

def run_telegram_bot():
    """Run Telegram bot polling - creates everything in this thread's event loop"""
    global bot_running, telegram_bot_app
    
    try:
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        logger.info("🤖 Initializing Telegram Bot in background thread...")
        
        # Import telegram bot
        try:
            from telegram_bot import (
                MatruRakshaBot,
                handle_switch_callback,
                handle_home_action,
                handle_document_upload,
                handle_text_message,
            )
            from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
            from telegram import Update
        except ImportError as e:
            logger.error(f"⚠️  Could not import telegram_bot: {e}")
            return
        
        # Build application IN THIS EVENT LOOP
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Create bot instance
        bot = MatruRakshaBot()
        
        # Setup handlers manually here (if bot doesn't have setup_handlers method)
        from telegram_bot import (
            AWAITING_NAME, AWAITING_AGE, AWAITING_PHONE, AWAITING_DUE_DATE,
            AWAITING_LOCATION, AWAITING_GRAVIDA, AWAITING_PARITY, AWAITING_BMI,
            AWAITING_LANGUAGE, CONFIRM_REGISTRATION
        )
        
        # Registration conversation handler
        registration_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(bot.button_callback, pattern="^(register|register_new)$")
            ],
            states={
                AWAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_name)],
                AWAITING_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_age)],
                AWAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_phone)],
                AWAITING_DUE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_due_date)],
                AWAITING_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_location)],
                AWAITING_GRAVIDA: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_gravida)],
                AWAITING_PARITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_parity)],
                AWAITING_BMI: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_bmi)],
                AWAITING_LANGUAGE: [
                    CallbackQueryHandler(bot.receive_language, pattern="^lang_"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_language),
                ],
                CONFIRM_REGISTRATION: [CallbackQueryHandler(bot.confirm_registration, pattern="^confirm_")]
            },
            fallbacks=[CommandHandler('cancel', bot.cancel_registration)],
            name="registration",
            persistent=False,
            per_message=False
        )
        
        # Add handlers
        application.add_handler(CommandHandler("start", bot.start))
        application.add_handler(registration_handler)
        application.add_handler(CallbackQueryHandler(handle_switch_callback, pattern=r"^switch_mother_"))
        application.add_handler(CallbackQueryHandler(handle_home_action, pattern=r"^action_"))
        application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document_upload))
        # Add text message handler for queries (but not during registration)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        
        # Initialize the application
        loop.run_until_complete(application.initialize())
        
        logger.info("✅ Telegram Bot initialized successfully")
        
        # Store globally
        telegram_bot_app = application
        
        # Start polling
        logger.info("🚀 Starting Telegram polling...")
        bot_running = True
        
        loop.run_until_complete(application.start())
        loop.run_until_complete(application.updater.start_polling(
            drop_pending_updates=True
        ))
        
        logger.info("✅ Telegram polling started")
        logger.info("🤖 MatruRaksha Telegram Bot is ACTIVE")
        
        # Keep running
        loop.run_forever()
        
    except Exception as e:
        logger.error(f"❌ Error in Telegram polling: {e}", exc_info=True)
        bot_running = False
    finally:
        try:
            if telegram_bot_app:
                loop.run_until_complete(telegram_bot_app.updater.stop())
                loop.run_until_complete(telegram_bot_app.stop())
                loop.run_until_complete(telegram_bot_app.shutdown())
        except:
            pass
        loop.close()


async def stop_telegram_bot():
    """Properly stop the Telegram bot"""
    global bot_running
    
    if bot_running:
        try:
            logger.info("🛑 Stopping Telegram bot...")
            bot_running = False
            
            # Give the thread time to clean up
            await asyncio.sleep(1)
            
            logger.info("🛑 Telegram bot stopped")
        except Exception as e:
            logger.error(f"Error stopping Telegram bot: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for FastAPI"""
    # ==================== STARTUP ====================
    logger.info("=" * 60)
    logger.info("")
    logger.info("    ╔══════════════════════════════════════════════════╗")
    logger.info("    ║                                                  ║")
    logger.info("    ║           🤰 MatruRaksha AI System 🤰           ║")
    logger.info("    ║                                                  ║")
    logger.info("    ║          Maternal Health Guardian System        ║")
    logger.info("    ║                                                  ║")
    logger.info("    ╚══════════════════════════════════════════════════╝")
    logger.info("")
    logger.info("=" * 60)
    
    # Start Telegram bot in background thread
    if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != "placeholder":
        global bot_thread
        bot_thread = threading.Thread(
            target=run_telegram_bot,
            daemon=True,
            name="TelegramBotThread"
        )
        bot_thread.start()
        
        # Give it a moment to initialize
        await asyncio.sleep(2)
        
        logger.info("")
        logger.info("    ✅ Services Status:")
        logger.info("")
        logger.info("    🤖 Telegram Bot: Running in background")
        logger.info("    🚀 Starting FastAPI Backend...")
        logger.info("")
    else:
        logger.warning("    ⚠️  Telegram Bot Token not set")
        logger.info("    🚀 Starting FastAPI Backend only...")
    
    yield
    
    # ==================== SHUTDOWN ====================
    logger.info("=" * 60)
    logger.info("🛑 Shutting down MatruRaksha AI System...")
    
    await stop_telegram_bot()
    
    logger.info("✅ Shutdown complete")
    logger.info("=" * 60)


# ==================== CREATE FASTAPI APP ====================
# Mount enhanced API router
from enhanced_api import router as enhanced_router
app = FastAPI(title="MatruRaksha AI Backend", lifespan=lifespan)
app.include_router(enhanced_router)
# ==================== CORS SETUP ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== HELPER FUNCTIONS ====================

def calculate_risk_score(assessment: RiskAssessment) -> dict:
    """Calculate risk score based on vital signs and symptoms"""
    risk_score = 0.0
    risk_factors = []
    
    # Blood Pressure Risk
    if assessment.systolic_bp and assessment.diastolic_bp:
        if assessment.systolic_bp >= 160 or assessment.diastolic_bp >= 110:
            risk_score += 0.3
            risk_factors.append("Severe Hypertension")
        elif assessment.systolic_bp >= 140 or assessment.diastolic_bp >= 90:
            risk_score += 0.2
            risk_factors.append("Hypertension")
    
    # Hemoglobin Risk
    if assessment.hemoglobin:
        if assessment.hemoglobin < 7:
            risk_score += 0.3
            risk_factors.append("Severe Anemia")
        elif assessment.hemoglobin < 10:
            risk_score += 0.2
            risk_factors.append("Anemia")
    
    # Blood Glucose Risk
    if assessment.blood_glucose:
        if assessment.blood_glucose > 200:
            risk_score += 0.2
            risk_factors.append("Hyperglycemia")
    
    # Clinical Symptoms
    if assessment.proteinuria == 1:
        risk_score += 0.15
        risk_factors.append("Proteinuria")
    if assessment.edema == 1:
        risk_score += 0.1
        risk_factors.append("Edema")
    if assessment.headache == 1:
        risk_score += 0.1
        risk_factors.append("Headache")
    if assessment.vision_changes == 1:
        risk_score += 0.2
        risk_factors.append("Vision Changes")
    if assessment.epigastric_pain == 1:
        risk_score += 0.15
        risk_factors.append("Epigastric Pain")
    if assessment.vaginal_bleeding == 1:
        risk_score += 0.25
        risk_factors.append("Vaginal Bleeding")
    
    # Cap risk score at 1.0
    risk_score = min(risk_score, 1.0)
    
    # Determine risk level
    if risk_score >= 0.7:
        risk_level = "HIGH"
    elif risk_score >= 0.4:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"
    
    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_factors": risk_factors
    }


def calculate_pregnancy_week(registration_date: str) -> int:
    """Calculate current pregnancy week from registration"""
    try:
        reg_date = datetime.fromisoformat(registration_date.replace('Z', '+00:00'))
        days_since = (datetime.now() - reg_date).days
        return 8 + (days_since // 7)
    except:
        return 20


async def run_ai_agent_assessment(mother_data: Dict, background_tasks: BackgroundTasks) -> Optional[Dict]:
    """Run AI agent assessment if agents are available"""
    if not AGENTS_AVAILABLE or not orchestrator:
        logger.info("ℹ️  AI Agents not available - skipping agent assessment")
        return None
    
    try:
        logger.info("🤖 Running AI Agent Orchestra...")
        assessment_result = await orchestrator.process_mother_data(mother_data)
        logger.info(f"✅ AI Assessment complete. Agents used: {assessment_result.get('agents_executed', [])}")
        return assessment_result
    except Exception as e:
        logger.error(f"❌ Error in AI agent assessment: {str(e)}", exc_info=True)
        return None


def analyze_document_with_gemini(file_url: str, file_type: str, mother_data: Dict) -> Dict[str, Any]:
    """Analyze medical document using Gemini AI"""
    
    analysis_result = {
        "status": "completed",
        "extracted_data": {},
        "concerns": [],
        "recommendations": [],
        "risk_level": "normal",
        "timestamp": datetime.now().isoformat()
    }
    
    if not GEMINI_AVAILABLE:
        logger.warning("⚠️  Gemini not available - returning basic analysis")
        analysis_result["status"] = "pending_review"
        analysis_result["extracted_data"] = {
            "note": "AI analysis not available - manual review required"
        }
        return analysis_result
    
    try:
        logger.info(f"🤖 Analyzing document with Gemini AI: {file_url}")
        
        # Create the prompt for Gemini
        prompt = f"""
You are a maternal health expert analyzing a medical report for a pregnant woman.

**Mother's Profile:**
- Name: {mother_data.get('name')}
- Age: {mother_data.get('age')} years
- Gravida: {mother_data.get('gravida')} (number of pregnancies)
- Parity: {mother_data.get('parity')} (number of live births)
- BMI: {mother_data.get('bmi')}
- Location: {mother_data.get('location')}

**Task:**
Analyze the medical report and extract the following information in a structured format:

1. **Key Health Metrics** (extract if present):
   - Hemoglobin level (g/dL)
   - Blood pressure (systolic/diastolic)
   - Blood sugar/glucose level (mg/dL)
   - Weight (kg)
   - Any other vital signs

2. **Health Concerns** (identify any abnormalities or risk factors):
   - List any concerning values or conditions
   - Rate severity: mild, moderate, severe

3. **Recommendations**:
   - What actions should be taken
   - Any follow-up needed
   - Dietary or lifestyle advice

4. **Risk Assessment**:
   - Overall risk level: low, moderate, or high
   - Reasoning for the risk level

**Output Format (JSON):**
{{
    "extracted_metrics": {{
        "hemoglobin": <value or null>,
        "blood_pressure_systolic": <value or null>,
        "blood_pressure_diastolic": <value or null>,
        "blood_sugar": <value or null>,
        "weight": <value or null>,
        "other_findings": "<any other important findings>"
    }},
    "concerns": [
        "<concern 1>",
        "<concern 2>"
    ],
    "recommendations": [
        "<recommendation 1>",
        "<recommendation 2>"
    ],
    "risk_level": "<low/moderate/high>",
    "risk_reasoning": "<explanation>"
}}

Provide ONLY the JSON output, no additional text.
"""
        
        # Try different model names (API versions vary)
        model_names = ['gemini-2.5-flash']
        model = None
        
        for model_name in model_names:
            try:
                model = genai.GenerativeModel(model_name)
                logger.info(f"✅ Using Gemini model: {model_name}")
                break
            except Exception as e:
                logger.warning(f"⚠️  Model {model_name} not available: {e}")
                continue
        
        if not model:
            raise Exception("No Gemini models available")
        
        # If it's an image, we can pass it directly to Gemini
        if file_type.startswith('image/'):
            try:
                # Download the image
                response = requests.get(file_url, timeout=30)
                response.raise_for_status()
                
                # Create image data for Gemini
                import PIL.Image
                import io
                image = PIL.Image.open(io.BytesIO(response.content))
                
                # Generate response with image
                response = model.generate_content([prompt, image])
                ai_response = response.text
                
            except Exception as img_error:
                logger.error(f"Error processing image: {img_error}")
                # Fallback to text-only analysis
                response = model.generate_content(prompt + f"\n\nNote: Could not load image from URL: {file_url}")
                ai_response = response.text
        else:
            # For PDFs and other documents, use text-only analysis
            # Note: For full PDF parsing, you'd need to extract text first
            response = model.generate_content(
                prompt + f"\n\nDocument URL: {file_url}\nFile Type: {file_type}\n\n"
                "Note: Please provide a general analysis based on typical maternal health reports."
            )
            ai_response = response.text
        
        logger.info(f"✅ Gemini response received: {len(ai_response)} characters")
        
        # Parse the JSON response
        import json
        import re
        
        # Extract JSON from response (sometimes Gemini wraps it in markdown)
        json_match = re.search(r'\{.*\}', ai_response, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            parsed_data = json.loads(json_str)
            
            # Update analysis result with parsed data
            analysis_result["extracted_data"] = parsed_data.get("extracted_metrics", {})
            analysis_result["concerns"] = parsed_data.get("concerns", [])
            analysis_result["recommendations"] = parsed_data.get("recommendations", [])
            analysis_result["risk_level"] = parsed_data.get("risk_level", "normal")
            analysis_result["risk_reasoning"] = parsed_data.get("risk_reasoning", "")
            analysis_result["ai_analysis"] = ai_response
            analysis_result["analyzed_with"] = "Google Gemini AI"
            
            logger.info(f"✅ Analysis complete - Risk Level: {analysis_result['risk_level']}")
        else:
            # If JSON parsing fails, store raw response
            analysis_result["ai_analysis"] = ai_response
            analysis_result["extracted_data"] = {
                "note": "Manual review needed - AI response format unexpected"
            }
            analysis_result["status"] = "pending_review"
            logger.warning("⚠️  Could not parse Gemini response as JSON")
    
    except Exception as e:
        logger.error(f"❌ Gemini analysis error: {e}", exc_info=True)
        analysis_result["status"] = "error"
        analysis_result["error"] = str(e)
        analysis_result["extracted_data"] = {
            "note": "Analysis failed - manual review required"
        }
    
    return analysis_result


# ==================== HEALTH CHECK ====================
@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "MatruRaksha AI Backend",
        "version": "2.0.0",
        "supabase_connected": supabase is not None,
        "telegram_bot_token": "✅ Set" if TELEGRAM_BOT_TOKEN != "placeholder" else "❌ Not Set",
        "telegram_polling": "🟢 Active" if bot_running else "🔴 Inactive",
        "gemini_ai": "🤖 Active" if GEMINI_AVAILABLE else "❌ Not Available",
        "ai_agents": "🤖 Active" if AGENTS_AVAILABLE else "❌ Not Loaded"
    }


# ==================== MOTHER ENDPOINTS ====================

@app.post("/mothers/register")
async def register_mother(mother: Mother, background_tasks: BackgroundTasks):
    """Register a new pregnant mother"""
    try:
        logger.info(f"📝 Registering mother: {mother.name}")
        
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        # Insert into database
        insert_data = {
            "name": mother.name,
            "phone": mother.phone,
            "age": mother.age,
            "gravida": mother.gravida,
            "parity": mother.parity,
            "bmi": mother.bmi,
            "location": mother.location,
            "preferred_language": mother.preferred_language,
            "telegram_chat_id": mother.telegram_chat_id,
            "due_date": mother.due_date,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase.table("mothers").insert(insert_data).execute()
        
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to register mother"
            )
        
        mother_id = result.data[0]["id"]
        logger.info(f"✅ Mother registered successfully: {mother_id}")
        
        return {
            "status": "success",
            "message": "Mother registered successfully",
            "mother_id": mother_id,
            "data": result.data[0]
        }
    
    except Exception as e:
        logger.error(f"❌ Error registering mother: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error registering mother: {str(e)}"
        )


@app.get("/mothers")
def get_all_mothers():
    """Get all registered mothers"""
    try:
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        result = supabase.table("mothers").select("*").execute()
        logger.info(f"✅ Retrieved {len(result.data)} mothers")
        
        return {
            "status": "success",
            "count": len(result.data),
            "data": result.data
        }
    except Exception as e:
        logger.error(f"❌ Error fetching mothers: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching mothers: {str(e)}"
        )


@app.get("/mothers/{mother_id}")
def get_mother(mother_id: str):
    """Get specific mother by ID"""
    try:
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        result = supabase.table("mothers").select("*").eq("id", mother_id).execute()
        
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mother with ID {mother_id} not found"
            )
        
        return {
            "status": "success",
            "data": result.data[0]
        }
    except Exception as e:
        logger.error(f"❌ Error fetching mother: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching mother: {str(e)}"
        )


# ==================== DOCUMENT ANALYSIS ENDPOINTS ====================

@app.post("/analyze-report")
async def analyze_report(request: DocumentAnalysisRequest, background_tasks: BackgroundTasks):
    """Analyze uploaded medical report using Gemini AI"""
    try:
        logger.info(f"🔍 Analyzing report {request.report_id} for mother {request.mother_id}")
        
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        # Get mother data
        mother_result = supabase.table("mothers").select("*").eq("id", request.mother_id).execute()
        
        if not mother_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Mother not found"
            )
        
        mother_data = mother_result.data[0]
        
        # Update report status to processing
        supabase.table("medical_reports").update({
            "analysis_status": "processing"
        }).eq("id", request.report_id).execute()
        
        # Perform Gemini AI analysis
        analysis_result = analyze_document_with_gemini(
            request.file_url,
            request.file_type,
            mother_data
        )
        
        # Update report with analysis results
        update_data = {
            "analysis_status": analysis_result.get("status", "completed"),
            "analysis_result": analysis_result,
            "analyzed_at": datetime.now().isoformat()
        }
        
        # Extract key metrics if available
        extracted_data = analysis_result.get("extracted_data", {})
        if extracted_data:
            update_data["extracted_metrics"] = extracted_data
        
        # Update medical_reports table
        report_update = supabase.table("medical_reports").update(update_data).eq("id", request.report_id).execute()
        
        logger.info(f"✅ Report analysis completed: {analysis_result.get('status')}")
        
        # Check for high-risk concerns and send alerts
        concerns = analysis_result.get("concerns", [])
        risk_level = analysis_result.get("risk_level", "normal")
        
        # Send Telegram notification if high risk
        if (risk_level in ["high", "moderate"] or concerns) and mother_data.get("telegram_chat_id"):
            try:
                # Import telegram service
                from services.telegram_service import telegram_service
                
                concerns_text = "\n".join([f"• {c}" for c in concerns[:3]]) if concerns else "None"
                recommendations_text = "\n".join([f"• {r}" for r in analysis_result.get("recommendations", [])[:3]])
                
                message = (
                    f"🔍 *Report Analysis Complete*\n\n"
                    f"📊 Risk Level: *{risk_level.upper()}*\n\n"
                )
                
                if concerns:
                    message += f"⚠️ *Concerns:*\n{concerns_text}\n\n"
                
                if recommendations_text:
                    message += f"💡 *Recommendations:*\n{recommendations_text}\n\n"
                
                message += "Please consult with your healthcare provider for detailed guidance."
                
                telegram_service.send_message(
                    chat_id=mother_data["telegram_chat_id"],
                    message=message
                )
                logger.info("✅ Alert sent to Telegram")
            except Exception as telegram_error:
                logger.error(f"⚠️  Telegram notification failed: {telegram_error}")
        
        return {
            "success": True,
            "message": "Report analyzed successfully",
            "status": analysis_result.get("status"),
            "risk_level": analysis_result.get("risk_level"),
            "concerns": analysis_result.get("concerns", []),
            "recommendations": analysis_result.get("recommendations", []),
            "analysis": analysis_result
        }
    
    except Exception as e:
        logger.error(f"❌ Report analysis error: {e}", exc_info=True)
        
        # Update status to error
        if supabase:
            supabase.table("medical_reports").update({
                "analysis_status": "error",
                "error_message": str(e)
            }).eq("id", request.report_id).execute()
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}"
        )


@app.get("/reports/{mother_id}")
def get_mother_reports(mother_id: str):  # Changed from int to str
    """Get all reports for a specific mother"""
    try:
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        result = supabase.table("medical_reports").select("*").eq("mother_id", mother_id).order("uploaded_at", desc=True).execute()
        
        return {
            "success": True,
            "count": len(result.data) if result.data else 0,
            "data": result.data
        }
    except Exception as e:
        logger.error(f"❌ Error fetching reports: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@app.get("/reports/telegram/{telegram_chat_id}")
def get_reports_by_telegram(telegram_chat_id: str):
    """Get all reports for a Telegram user"""
    try:
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        result = supabase.table("medical_reports").select("*").eq("telegram_chat_id", telegram_chat_id).order("uploaded_at", desc=True).execute()
        
        return {
            "success": True,
            "count": len(result.data) if result.data else 0,
            "data": result.data
        }
    except Exception as e:
        logger.error(f"❌ Error fetching reports: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ==================== RISK ASSESSMENT ENDPOINTS ====================

@app.post("/risk/assess")
async def assess_risk(assessment: RiskAssessment, background_tasks: BackgroundTasks):
    """Assess pregnancy risk for a mother"""
    try:
        logger.info(f"⚠️ Assessing risk for mother: {assessment.mother_id}")
        
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        # Verify mother exists
        mother_result = supabase.table("mothers").select("*").eq("id", assessment.mother_id).execute()
        if not mother_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mother with ID {assessment.mother_id} not found"
            )
        
        mother_data = mother_result.data[0]
        
        # Calculate risk score
        risk_calculation = calculate_risk_score(assessment)
        logger.info(f"📈 Risk calculation: {risk_calculation}")
        
        # Save assessment to database
        insert_data = {
            "mother_id": assessment.mother_id,
            "systolic_bp": assessment.systolic_bp,
            "diastolic_bp": assessment.diastolic_bp,
            "heart_rate": assessment.heart_rate,
            "blood_glucose": assessment.blood_glucose,
            "hemoglobin": assessment.hemoglobin,
            "proteinuria": assessment.proteinuria,
            "edema": assessment.edema,
            "headache": assessment.headache,
            "vision_changes": assessment.vision_changes,
            "epigastric_pain": assessment.epigastric_pain,
            "vaginal_bleeding": assessment.vaginal_bleeding,
            "risk_score": float(risk_calculation["risk_score"]),
            "risk_level": str(risk_calculation["risk_level"]),
            "notes": assessment.notes,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase.table("risk_assessments").insert(insert_data).execute()
        logger.info(f"✅ Risk assessment saved: {risk_calculation['risk_level']}")
        
        # Send alert if high risk
        if risk_calculation["risk_level"] == "HIGH" and mother_data.get("telegram_chat_id"):
            try:
                from services.telegram_service import telegram_service
                
                risk_factors_text = "\n".join([f"• {rf}" for rf in risk_calculation["risk_factors"]])
                
                telegram_service.send_message(
                    chat_id=mother_data["telegram_chat_id"],
                    message=f"⚠️ *HIGH RISK ALERT*\n\n"
                            f"Risk Score: {risk_calculation['risk_score']:.2f}\n\n"
                            f"*Risk Factors:*\n{risk_factors_text}\n\n"
                            f"⚕️ Please consult with your healthcare provider immediately."
                )
            except Exception as telegram_error:
                logger.error(f"⚠️  Telegram alert failed: {telegram_error}")
        
        return {
            "status": "success",
            "message": f"Risk assessment completed - {risk_calculation['risk_level']} RISK",
            "risk_score": risk_calculation["risk_score"],
            "risk_level": risk_calculation["risk_level"],
            "risk_factors": risk_calculation["risk_factors"],
            "data": result.data[0] if result.data else None
        }
    
    except Exception as e:
        logger.error(f"❌ Error assessing risk: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error assessing risk: {str(e)}"
        )


@app.get("/risk/mother/{mother_id}")
def get_mother_risk(mother_id: str):
    """Get risk assessments for a specific mother"""
    try:
        if not supabase:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase not connected"
            )
        
        result = supabase.table("risk_assessments").select("*").eq("mother_id", mother_id).order("created_at", desc=True).execute()
        
        return {
            "status": "success",
            "count": len(result.data),
            "data": result.data
        }
    except Exception as e:
        logger.error(f"❌ Error fetching risk assessments: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching risk assessments: {str(e)}"
        )


# ==================== ANALYTICS ENDPOINTS ====================

@app.get("/analytics/dashboard")
def get_dashboard_analytics():
    """Get dashboard analytics"""
    try:
        if not supabase:
            return {
                "status": "success",
                "total_mothers": 0,
                "high_risk_count": 0,
                "moderate_risk_count": 0,
                "low_risk_count": 0,
                "total_assessments": 0,
                "total_reports": 0,
                "timestamp": datetime.now().isoformat()
            }
        
        # Get all mothers
        mothers_result = supabase.table("mothers").select("*").execute()
        total_mothers = len(mothers_result.data) if mothers_result.data else 0
        
        # Get all risk assessments
        assessments_result = supabase.table("risk_assessments").select("*").execute()
        assessments = assessments_result.data if assessments_result.data else []
        
        # Get all reports
        reports_result = supabase.table("medical_reports").select("*").execute()
        total_reports = len(reports_result.data) if reports_result.data else 0
        
        # Count risk levels
        high_risk = sum(1 for a in assessments if a.get("risk_level") == "HIGH")
        moderate_risk = sum(1 for a in assessments if a.get("risk_level") == "MODERATE")
        low_risk = sum(1 for a in assessments if a.get("risk_level") == "LOW")
        
        return {
            "status": "success",
            "total_mothers": total_mothers,
            "high_risk_count": high_risk,
            "moderate_risk_count": moderate_risk,
            "low_risk_count": low_risk,
            "total_assessments": len(assessments),
            "total_reports": total_reports,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error fetching analytics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching analytics: {str(e)}"
        )


# ==================== ROOT ENDPOINT ====================
@app.get("/")
def root():
    """Root endpoint"""
    return {
        "message": "MatruRaksha AI Backend API",
        "version": "2.0.0",
        "status": "running",
        "docs": "/docs",
        "telegram_bot": "🟢 Active" if bot_running else "🔴 Inactive",
        "gemini_ai": "🤖 Active" if GEMINI_AVAILABLE else "❌ Not Available",
        "ai_agents": "🤖 Active" if AGENTS_AVAILABLE else "❌ Not Loaded"
    }


# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "=" * 60)
    print("🚀 Starting MatruRaksha AI Backend...")
    print("=" * 60)
    print(f"📌 Supabase URL: {SUPABASE_URL}")
    print(f"📱 Telegram Bot Token: {'✅ Set' if TELEGRAM_BOT_TOKEN != 'placeholder' else '❌ Not Set'}")
    print(f"🤖 Gemini AI: {'✅ Available' if GEMINI_AVAILABLE else '❌ Not Available'}")
    print("=" * 60)
    print()
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
