import { useEffect, useState } from 'react';

import StaffdeckIcon from '@/components/StaffdeckIcon';
import { notify } from '@/components/ui/app-toast';
import { api } from '@/api/client';
import type { HarnessWorkspaceArtifact } from '@/types';

import {
  CHAT_ARTIFACTS_CLASS,
  CHAT_ARTIFACT_BUTTON_CLASS,
  CHAT_ARTIFACT_COPY_CLASS,
  CHAT_ARTIFACT_HEADING_CLASS,
  CHAT_ARTIFACT_ICON_CLASS,
  CHAT_ARTIFACT_IMAGE_CARD_CLASS,
  CHAT_ARTIFACT_IMAGE_CLASS,
  CHAT_ARTIFACT_IMAGE_DOWNLOAD_CLASS,
  CHAT_ARTIFACT_IMAGE_FOOTER_CLASS,
  CHAT_ARTIFACT_IMAGE_LINK_CLASS,
  CHAT_ARTIFACT_IMAGE_PLACEHOLDER_CLASS,
  CHAT_ARTIFACT_LIST_CLASS,
  CHAT_ARTIFACT_META_CLASS,
  CHAT_ARTIFACT_NAME_CLASS,
} from '../chatPageStyles';

type HarnessArtifactDownloadsProps = {
  artifacts: HarnessWorkspaceArtifact[];
  tenantId: string;
  sessionId: string;
};

/**
 * 对话生成文件下载区：列出本轮产物，图片项附带缩略图预览。
 *
 * 无产物时返回 null；租户或会话缺失时禁用下载，避免请求非法地址。
 */
export default function HarnessArtifactDownloads({
  artifacts,
  tenantId,
  sessionId,
}: HarnessArtifactDownloadsProps) {
  const [downloading, setDownloading] = useState('');

  if (artifacts.length === 0) return null;

  /**
   * 下载单个产物：拉取二进制内容后触发浏览器保存。
   *
   * 下载期间按“任务帧 + 路径”标识当前项，结束后无论成败都会清除该状态。
   */
  async function downloadArtifact(artifact: HarnessWorkspaceArtifact) {
    const identity = `${artifact.task_frame_id}\u001f${artifact.path}`;
    const filename = artifactFilename(artifact.display_name || artifact.path);
    setDownloading(identity);
    try {
      const blob = await api.blob(artifactApiPath(artifact, tenantId, sessionId));
      const objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(objectUrl);
      notify.success(`已下载文件：${filename}`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '文件下载失败');
    } finally {
      setDownloading('');
    }
  }

  return (
    <div className={CHAT_ARTIFACTS_CLASS} aria-label="生成文件">
      <div className={CHAT_ARTIFACT_HEADING_CLASS}>
        <StaffdeckIcon name="folder" size={14} />
        <span>生成文件</span>
      </div>
      <div className={CHAT_ARTIFACT_LIST_CLASS}>
        {artifacts.map((artifact) => {
          const identity = `${artifact.task_frame_id}\u001f${artifact.path}`;
          const filename = artifactFilename(artifact.display_name || artifact.path);
          const isDownloading = downloading === identity;
          if (isImageArtifact(artifact)) {
            return (
              <ArtifactImagePreview
                artifact={artifact}
                identity={identity}
                filename={filename}
                isDownloading={isDownloading}
                key={identity}
                tenantId={tenantId}
                sessionId={sessionId}
                onDownload={() => void downloadArtifact(artifact)}
              />
            );
          }
          return (
            <button
              type="button"
              className={CHAT_ARTIFACT_BUTTON_CLASS}
              key={identity}
              disabled={isDownloading || !sessionId || !tenantId}
              aria-label={`下载文件 ${filename}`}
              aria-busy={isDownloading}
              onClick={() => void downloadArtifact(artifact)}
            >
              <span className={CHAT_ARTIFACT_ICON_CLASS}>
                <StaffdeckIcon name="file" size={17} />
              </span>
              <span className={CHAT_ARTIFACT_COPY_CLASS}>
                <span className={CHAT_ARTIFACT_NAME_CLASS} data-i18n-ignore>
                  {filename}
                </span>
                <span className={CHAT_ARTIFACT_META_CLASS}>
                  {isDownloading ? '下载中' : artifactMeta(artifact)}
                </span>
              </span>
              <StaffdeckIcon name="download" size={16} />
            </button>
          );
        })}
      </div>
    </div>
  );
}

type ArtifactImagePreviewProps = {
  artifact: HarnessWorkspaceArtifact;
  identity: string;
  filename: string;
  isDownloading: boolean;
  tenantId: string;
  sessionId: string;
  onDownload: () => void;
};

/**
 * 图片产物预览卡片：按需拉取 Blob 并生成临时 object URL。
 *
 * 依赖变化或组件卸载时回收 object URL；加载失败时展示可下载的兜底文案。
 */
function ArtifactImagePreview({
  artifact,
  identity,
  filename,
  isDownloading,
  tenantId,
  sessionId,
  onDownload,
}: ArtifactImagePreviewProps) {
  const [previewUrl, setPreviewUrl] = useState('');
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    if (!tenantId || !sessionId) return undefined;
    let disposed = false;
    let objectUrl = '';
    setPreviewUrl('');
    setPreviewFailed(false);

    void api.blob(artifactApiPath(artifact, tenantId, sessionId))
      .then((blob) => {
        if (disposed) return;
        objectUrl = window.URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      })
      .catch(() => {
        if (!disposed) setPreviewFailed(true);
      });

    return () => {
      disposed = true;
      if (objectUrl) window.URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.path, artifact.task_frame_id, identity, sessionId, tenantId]);

  return (
    <figure className={CHAT_ARTIFACT_IMAGE_CARD_CLASS}>
      {previewUrl ? (
        <a
          className={CHAT_ARTIFACT_IMAGE_LINK_CLASS}
          href={previewUrl}
          target="_blank"
          rel="noreferrer"
          aria-label={`查看图片 ${filename}`}
        >
          <img
            className={CHAT_ARTIFACT_IMAGE_CLASS}
            src={previewUrl}
            alt={filename}
            loading="lazy"
            decoding="async"
          />
        </a>
      ) : (
        <div className={CHAT_ARTIFACT_IMAGE_PLACEHOLDER_CLASS} aria-live="polite">
          {previewFailed ? '图片预览不可用，可下载查看' : '正在加载图片…'}
        </div>
      )}
      <figcaption className={CHAT_ARTIFACT_IMAGE_FOOTER_CLASS}>
        <span className={CHAT_ARTIFACT_COPY_CLASS}>
          <span className={CHAT_ARTIFACT_NAME_CLASS} data-i18n-ignore>{filename}</span>
          <span className={CHAT_ARTIFACT_META_CLASS}>{artifactMeta(artifact)}</span>
        </span>
        <button
          type="button"
          className={CHAT_ARTIFACT_IMAGE_DOWNLOAD_CLASS}
          disabled={isDownloading || !sessionId || !tenantId}
          aria-label={`下载图片 ${filename}`}
          aria-busy={isDownloading}
          onClick={onDownload}
        >
          <StaffdeckIcon name="download" size={16} />
        </button>
      </figcaption>
    </figure>
  );
}

/** 拼接产物下载接口地址，对会话 ID、任务帧 ID 与路径参数做编码。 */
function artifactApiPath(
  artifact: HarnessWorkspaceArtifact,
  tenantId: string,
  sessionId: string,
): string {
  const query = new URLSearchParams({
    tenant_id: tenantId,
    path: artifact.path,
  });
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/artifacts/`
    + `${encodeURIComponent(artifact.task_frame_id)}?${query.toString()}`;
}

/** 从产物路径取出文件名，过滤控制字符并截断到 180 字符，缺省为 artifact。 */
function artifactFilename(path: string): string {
  const filename = path.replace(/\\/g, '/').split('/').pop()?.trim() || '';
  return filename.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 180) || 'artifact';
}

/** 判断产物是否为图片：优先看 content-type，其次按文件名后缀匹配。 */
function isImageArtifact(artifact: HarnessWorkspaceArtifact): boolean {
  const contentType = artifact.content_type?.toLowerCase().split(';')[0].trim();
  if (contentType?.startsWith('image/')) return true;
  return /\.(?:apng|avif|bmp|gif|jpe?g|png|svg|webp)$/i.test(
    artifact.display_name || artifact.path,
  );
}

/** 组合产物的描述与体积文案，缺少描述时回退为“生成文件”。 */
function artifactMeta(artifact: HarnessWorkspaceArtifact): string {
  const size = formatArtifactSize(artifact.size);
  const description = artifact.description?.trim();
  if (description && size) return `${description} · ${size}`;
  if (description) return description;
  return size ? `生成文件 · ${size}` : '生成文件';
}

/** 将字节数格式化为 B/KB/MB 文案，非法或负数返回空字符串。 */
function formatArtifactSize(size: number | null | undefined): string {
  if (typeof size !== 'number' || !Number.isFinite(size) || size < 0) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
