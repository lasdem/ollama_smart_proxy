#!/usr/bin/env python3
"""
Data Access Layer
Provides abstracted database operations for different database types
"""
import os
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from database import RequestLog, get_db, get_analytics

# Configure logging
logger = logging.getLogger(__name__)


class RequestLogRepository:
    """Repository for request log data access"""
    
    def __init__(self):
        self.db = get_db() 
    
    def create_request_log(self, request_data: Dict[str, Any]) -> RequestLog:
        """
        Create a new request log record
        
        Args:
            request_data: Dictionary containing request data
            
        Returns:
            RequestLog: Created request log object
        """
        session = None
        try:
            session = self.db.get_session()
            # Create request log
            request_log = RequestLog(
                request_id=request_data.get('request_id'),
                source_ip=request_data.get('source_ip'),
                model_name=request_data.get('model_name'),
                prompt_text=request_data.get('prompt_text'),
                timestamp_received=request_data.get('timestamp_received'),
                status=request_data.get('status'),
                priority_score=request_data.get('priority_score')
            )
            session.add(request_log)
            session.commit()
            session.refresh(request_log)
            return request_log
        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"Failed to create request log: {e}")
            # Fallback to file-based logging
            request_data['created_at'] = datetime.utcnow()
            self.db.write_to_fallback_file(request_data)
            # Return a mock object to indicate the request was logged (to fallback)
            return RequestLog(
                request_id=request_data.get('request_id'),
                source_ip=request_data.get('source_ip'),
                model_name=request_data.get('model_name'),
                prompt_text=request_data.get('prompt_text'),
                timestamp_received=request_data.get('timestamp_received'),
                status=request_data.get('status'),
                priority_score=request_data.get('priority_score'),
                created_at=datetime.utcnow()
            )
        finally:
            if session:
                session.close()
    
    def update_request_log(self, request_id: str, update_data: Dict[str, Any]) -> Optional[RequestLog]:
        """
        Update an existing request log record
        
        Args:
            request_id: Request ID to update
            update_data: Dictionary containing update data
            
        Returns:
            RequestLog: Updated request log object or None if not found
        """
        try:
            session = self.db.get_session()
            
            request_log = session.query(RequestLog).filter_by(request_id=request_id).first()
            if not request_log:
                return None
            
            # Update fields
            if 'response_text' in update_data:
                request_log.response_text = update_data['response_text']
            if 'timestamp_started' in update_data:
                request_log.timestamp_started = update_data['timestamp_started']
            if 'timestamp_completed' in update_data:
                request_log.timestamp_completed = update_data['timestamp_completed']
            if 'duration_seconds' in update_data:
                request_log.duration_seconds = update_data['duration_seconds']
            if 'queue_wait_seconds' in update_data:
                request_log.queue_wait_seconds = update_data['queue_wait_seconds']
            if 'processing_time_seconds' in update_data:
                request_log.processing_time_seconds = update_data['processing_time_seconds']
            if 'status' in update_data:
                request_log.status = update_data['status']
            if 'error_message' in update_data:
                request_log.error_message = update_data['error_message']
            
            session.commit()
            session.refresh(request_log)
            
            return request_log
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update request log: {e}")
            
            # Fallback to file-based logging for updates
            update_data['request_id'] = request_id
            update_data['created_at'] = datetime.utcnow().isoformat()
            self.db.write_to_fallback_file(update_data)
            
            # Return a mock object to indicate the update was logged (to fallback)
            return RequestLog(
                request_id=request_id,
                response_text=update_data.get('response_text'),
                timestamp_started=update_data.get('timestamp_started'),
                timestamp_completed=update_data.get('timestamp_completed'),
                duration_seconds=update_data.get('duration_seconds'),
                queue_wait_seconds=update_data.get('queue_wait_seconds'),
                processing_time_seconds=update_data.get('processing_time_seconds'),
                status=update_data.get('status'),
                error_message=update_data.get('error_message'),
                created_at=datetime.utcnow()
            )
        finally:
            session.close()

    def log_request(self, request_id: str, source_ip: str, model_name: str, status: str, duration_seconds: float, priority_score: int, prompt_text: Optional[str] = None, response_text: Optional[str] = None, timestamp_started: Optional[datetime] = None, queue_wait_seconds: Optional[float] = None, processing_time_seconds: Optional[float] = None, session_id: Optional[str] = None, outgoing_conversation_fingerprint: Optional[str] = None, endpoint: Optional[str] = None, user_agent: Optional[str] = None, thinking_text: Optional[str] = None, request_body: Optional[str] = None) -> Optional[RequestLog]:
        """
        Log or update a request

        Args:
            request_id: Request ID
            source_ip: Source IP address
            model_name: Model name
            status: Request status (queued, completed, error)
            duration_seconds: Duration in seconds
            priority_score: Priority score
            prompt_text: Optional prompt text
            response_text: Optional response text
            timestamp_started: Optional timestamp when processing started
            queue_wait_seconds: Optional queue wait time in seconds
            processing_time_seconds: Optional processing time in seconds
            session_id: Optional session id for conversation grouping
            outgoing_conversation_fingerprint: Optional hash of messages+response for matching next request's prefix
            endpoint: Optional API path e.g. /api/chat, /v1/chat/completions
            user_agent: Optional User-Agent request header
            thinking_text: Optional reasoning/thinking trace from thinking models
            request_body: Optional full request body (JSON string), e.g. for raw JSON view

        Returns:
            RequestLog: Request log object or None if not found
        """
        try:
            session = self.db.get_session()
            
            # Check if request log already exists
            request_log = session.query(RequestLog).filter_by(request_id=request_id).first()
            
            if request_log:
                # Update existing request log
                request_log.status = status
                request_log.duration_seconds = duration_seconds
                
                # Update optional fields if provided
                if prompt_text is not None:
                    request_log.prompt_text = prompt_text
                if response_text is not None:
                    request_log.response_text = response_text
                if timestamp_started is not None:
                    request_log.timestamp_started = timestamp_started
                if queue_wait_seconds is not None:
                    request_log.queue_wait_seconds = queue_wait_seconds
                if processing_time_seconds is not None:
                    request_log.processing_time_seconds = processing_time_seconds
                if session_id is not None:
                    request_log.session_id = session_id
                if outgoing_conversation_fingerprint is not None:
                    request_log.outgoing_conversation_fingerprint = outgoing_conversation_fingerprint
                if endpoint is not None:
                    request_log.endpoint = endpoint
                if user_agent is not None:
                    request_log.user_agent = user_agent
                if thinking_text is not None:
                    request_log.thinking_text = thinking_text
                if request_body is not None:
                    request_log.request_body = request_body

                if status == "completed":
                    request_log.timestamp_completed = datetime.utcnow()
                elif status == "error":
                    request_log.timestamp_completed = datetime.utcnow()
                    request_log.error_message = "Request completed with status: error"
            else:
                # Create new request log
                request_log = RequestLog(
                    request_id=request_id,
                    source_ip=source_ip,
                    model_name=model_name,
                    prompt_text=prompt_text,
                    status=status,
                    priority_score=priority_score,
                    timestamp_received=datetime.utcnow(),
                    duration_seconds=duration_seconds,
                    timestamp_started=timestamp_started,
                    queue_wait_seconds=queue_wait_seconds,
                    processing_time_seconds=processing_time_seconds,
                    session_id=session_id,
                    outgoing_conversation_fingerprint=outgoing_conversation_fingerprint,
                    timestamp_completed=datetime.utcnow() if status in ["completed", "error"] else None,
                    endpoint=endpoint,
                    user_agent=user_agent,
                    thinking_text=thinking_text,
                    request_body=request_body,
                )
                session.add(request_log)
            
            session.commit()
            session.refresh(request_log)
            
            return request_log
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to log request {request_id}: {e}")
            
            # Fallback to file-based logging
            request_data = {
                'request_id': request_id,
                'source_ip': source_ip,
                'model_name': model_name,
                'prompt_text': prompt_text,
                'response_text': response_text,
                'session_id': session_id,
                'outgoing_conversation_fingerprint': outgoing_conversation_fingerprint,
                'endpoint': endpoint,
                'user_agent': user_agent,
                'thinking_text': thinking_text,
                'timestamp_received': datetime.utcnow().isoformat() if not timestamp_started else timestamp_started.isoformat(),
                'timestamp_started': timestamp_started.isoformat() if timestamp_started else None,
                'timestamp_completed': datetime.utcnow().isoformat() if status in ["completed", "error"] else None,
                'duration_seconds': duration_seconds,
                'priority_score': priority_score,
                'queue_wait_seconds': queue_wait_seconds,
                'processing_time_seconds': processing_time_seconds,
                'status': status,
                'error_message': "Request completed with status: error" if status == "error" else None,
                'created_at': datetime.utcnow().isoformat()
            }
            
            self.db.write_to_fallback_file(request_data)
            
            # Return a mock object to indicate the request was logged (to fallback)
            return RequestLog(
                request_id=request_id,
                source_ip=source_ip,
                model_name=model_name,
                prompt_text=prompt_text,
                response_text=response_text,
                timestamp_received=datetime.utcnow(),
                timestamp_started=timestamp_started,
                timestamp_completed=datetime.utcnow() if status in ["completed", "error"] else None,
                duration_seconds=duration_seconds,
                priority_score=priority_score,
                queue_wait_seconds=queue_wait_seconds,
                processing_time_seconds=processing_time_seconds,
                status=status,
                error_message="Request completed with status: error" if status == "error" else None,
                session_id=session_id,
                outgoing_conversation_fingerprint=outgoing_conversation_fingerprint,
                created_at=datetime.utcnow()
            )
        finally:
            session.close()

    def get_request_by_ip_and_outgoing_fingerprint(self, source_ip: str, fingerprint: str) -> Optional[RequestLog]:
        """Return the most recent request from this IP with this outgoing_conversation_fingerprint (for session chaining)."""
        try:
            session = self.db.get_session()
            request_log = (
                session.query(RequestLog)
                .filter_by(source_ip=source_ip, outgoing_conversation_fingerprint=fingerprint)
                .order_by(RequestLog.timestamp_received.desc())
                .limit(1)
                .first()
            )
            return request_log
        except Exception as e:
            logger.error(f"Failed to get request by IP and fingerprint: {e}")
            raise
        finally:
            session.close()

    def get_request_log(self, request_id: str) -> Optional[RequestLog]:
        """
        Get a request log by ID
        
        Args:
            request_id: Request ID to retrieve
            
        Returns:
            RequestLog: Request log object or None if not found
        """
        try:
            session = self.db.get_session()
            
            request_log = session.query(RequestLog).filter_by(request_id=request_id).first()
            return request_log
        except Exception as e:
            logger.error(f"Failed to get request log: {e}")
            raise
        finally:
            session.close()
    
    def get_request_logs_by_model(self, model_name: str, limit: int = 100) -> List[RequestLog]:
        """
        Get request logs by model name
        
        Args:
            model_name: Model name to filter by
            limit: Maximum number of results
            
        Returns:
            List[RequestLog]: List of request log objects
        """
        try:
            session = self.db.get_session()
            
            request_logs = session.query(RequestLog).filter_by(model_name=model_name).order_by(RequestLog.timestamp_received.desc()).limit(limit).all()
            return request_logs
        except Exception as e:
            logger.error(f"Failed to get request logs by model: {e}")
            raise
        finally:
            session.close()
    
    def get_request_logs_by_ip(self, source_ip: str, limit: int = 100) -> List[RequestLog]:
        """
        Get request logs by IP address
        
        Args:
            source_ip: IP address to filter by
            limit: Maximum number of results
            
        Returns:
            List[RequestLog]: List of request log objects
        """
        try:
            session = self.db.get_session()
            
            request_logs = session.query(RequestLog).filter_by(source_ip=source_ip).order_by(RequestLog.timestamp_received.desc()).limit(limit).all()
            return request_logs
        except Exception as e:
            logger.error(f"Failed to get request logs by IP: {e}")
            raise
        finally:
            session.close()


class AnalyticsRepository:
    def get_error_rate_analysis(self, start_time: datetime, end_time: datetime, group_by: str = 'model_name') -> List[Dict[str, Any]]:
        """
        Get error rate analysis grouped by model or time
        Args:
            start_time: Start time for query
            end_time: End time for query
            group_by: 'model_name' or 'hour'
        Returns:
            List[Dict]: Error rate stats
        """
        return self.analytics.get_error_rate_analysis(start_time, end_time, group_by)
    """Repository for analytics data access"""
    
    def __init__(self):
        self.analytics = get_analytics()
    
    def get_request_count_by_model(self, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """
        Get request count by model
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            
        Returns:
            List[Dict]: List of model statistics
        """
        return self.analytics.get_request_count_by_model(start_time, end_time)
    
    def get_request_count_by_ip(self, start_time: datetime, end_time: datetime, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get request count by IP address
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            limit: Maximum number of results
            
        Returns:
            List[Dict]: List of IP statistics
        """
        return self.analytics.get_request_count_by_ip(start_time, end_time, limit)
    
    def get_performance_stats(self, start_time: datetime, end_time: datetime, group_by: str = 'model_name') -> List[Dict[str, Any]]:
        """
        Get performance statistics grouped by model or ip
        Args:
            start_time: Start time for query
            end_time: End time for query
            group_by: 'model_name' or 'ip'
        Returns:
            List[Dict]: Performance stats
        """
        return self.analytics.get_performance_stats(start_time, end_time, group_by)

    def get_average_duration_by_model(self, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """
        Get average duration by model
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            
        Returns:
            List[Dict]: List of model duration statistics
        """
        return self.analytics.get_average_duration_by_model(start_time, end_time)
    
    def get_token_usage_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """
        Get token usage statistics
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            
        Returns:
            Dict: Token usage statistics
        """
        return self.analytics.get_token_usage_stats(start_time, end_time)
    
    def get_requests_over_time(self, interval: str = 'hour') -> List[Dict[str, Any]]:
        """
        Get request count over time
        
        Args:
            interval: Time interval ('hour', 'day', 'week')
            
        Returns:
            List[Dict]: Request count over time
        """
        return self.analytics.get_requests_over_time(interval)
    
    def get_priority_score_distribution(self, start_time: datetime, end_time: datetime, group_by: str = 'model_name') -> List[Dict[str, Any]]:
        """
        Get priority score distribution (histogram, avg, min, max) grouped by model or time
        Args:
            start_time: Start time for query
            end_time: End time for query
            group_by: 'model_name' or 'hour'
        Returns:
            List[Dict]: Distribution stats
        """
        return self.analytics.get_priority_score_distribution(start_time, end_time, group_by)

    def get_model_bunching_detection(self, start_time: datetime, end_time: datetime, time_window_seconds: int = 60) -> List[Dict[str, Any]]:
        """
        Detect model bunching - when multiple requests for the same model arrive close together
        
        Args:
            start_time: Start time for query
            end_time: End time for query
            time_window_seconds: Time window in seconds to detect bunching (default: 60s)
            
        Returns:
            List[Dict]: Model bunching statistics
        """
        return self.analytics.get_model_bunching_detection(start_time, end_time, time_window_seconds)


# Global repository instances
request_log_repository = None
analytics_repository = None


def init_repositories():
    """Initialize all repositories"""
    global request_log_repository, analytics_repository
    request_log_repository = RequestLogRepository()
    analytics_repository = AnalyticsRepository()
    logger.info("Repositories initialized")


def get_request_log_repo() -> RequestLogRepository:
    """Get request log repository"""
    if request_log_repository is None:
        init_repositories()
    return request_log_repository


def get_analytics_repo() -> AnalyticsRepository:
    """Get analytics repository"""
    if analytics_repository is None:
        init_repositories()
    return analytics_repository
