import React from 'react';

export function SkeletonBar({ width = '100%', height = '16px', className = '' }: { width?: string; height?: string; className?: string }) {
  return <div className={`skeleton-bar ${className}`} style={{ width, height }} aria-hidden="true" />;
}

export function SkeletonCard({ height = '160px', className = '' }: { height?: string; className?: string }) {
  return (
    <div className={`panel skeleton-card ${className}`} style={{ minHeight: height }} aria-hidden="true">
      <div className="skeleton-header">
        <SkeletonBar width="35%" height="20px" />
        <SkeletonBar width="15%" height="16px" />
      </div>
      <div className="skeleton-body">
        <SkeletonBar width="85%" height="14px" />
        <SkeletonBar width="60%" height="14px" />
        <SkeletonBar width="75%" height="14px" />
      </div>
    </div>
  );
}

export function SkeletonPanel({ title, description }: { title?: string; description?: string }) {
  return (
    <div className="skeleton-panel-wrapper" role="status" aria-label={title ? `正在加载 ${title}…` : '正在加载内容…'}>
      <div className="skeleton-panel-header">
        {title && <h2>{title}</h2>}
        {description && <p className="skeleton-caption">{description}</p>}
      </div>
      <div className="skeleton-grid">
        <SkeletonCard height="180px" />
        <SkeletonCard height="180px" />
        <SkeletonCard height="240px" />
        <SkeletonCard height="240px" />
      </div>
    </div>
  );
}

export default SkeletonPanel;
