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
            session.rollback()
            logger.error(f"Failed to create request log: {e}")
            raise
        finally:
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
            raise
        finally:
            session.close()

    def log_request(self, request_id: str, source_ip: str, model_name: str, status: str, duration_seconds: float, priority_score: int) -> Optional[RequestLog]:
        """
        Log or update a request
        
        Args:
            request_id: Request ID
            source_ip: Source IP address
            model_name: Model name
            status: Request status (queued, completed, error)
            duration_seconds: Duration in seconds
            priority_score: Priority score
            
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
                request_log.timestamp_completed = datetime.utcnow()
                if status == "error":
                    request_log.error_message = "Request completed with status: error"
            else:
                # Create new request log
                request_log = RequestLog(
                    request_id=request_id,
                    source_ip=source_ip,
                    model_name=model_name,
                    status=status,
                    priority_score=priority_score,
                    timestamp_received=datetime.utcnow(),
                    duration_seconds=duration_seconds,
                    timestamp_completed=datetime.utcnow()
                )
                session.add(request_log)
            
            session.commit()
            session.refresh(request_log)
            
            return request_log
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to log request {request_id}: {e}")
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
