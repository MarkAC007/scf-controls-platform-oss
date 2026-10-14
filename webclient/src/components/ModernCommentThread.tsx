import React, { useState, useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';
import { apiClient } from '../data/apiClient';
import { useConfirm } from '../contexts/ConfirmContext';

// Configure DOMPurify for safe HTML rendering
// Only allow specific tags and attributes we generate in renderContent
const DOMPURIFY_CONFIG = {
  ALLOWED_TAGS: ['span', 'strong', 'em', 'code'] as string[],
  ALLOWED_ATTR: ['style'] as string[],
  ALLOW_DATA_ATTR: false,
};

// Mention and code highlights are built as an HTML string and pass through
// DOMPurify, which keeps only the `style` attribute (see DOMPURIFY_CONFIG), so
// they carry theme tokens inline rather than a class.
const MENTION_STYLE =
  'background-color: var(--primary-bg); color: var(--primary-ink); padding: 2px 6px; border-radius: 3px; font-weight: 600;';

const AVATAR_TONES = ['blue', 'green', 'orange', 'purple', 'teal', 'pink'] as const;

interface User {
  id: string;
  email: string;
  display_name: string | null;
}

interface Comment {
  id: string;
  content: string;
  mentions: string[];
  user: User;
  parent_comment_id?: string;
  created_at: string;
  is_edited: boolean;
  edited_at: string | null;
}

interface ModernCommentThreadProps {
  commentableType: 'control' | 'evidence' | 'task';
  commentableId: string;
  organizationId: string;
  readOnly?: boolean;
  /**
   * 'plain' (default): an h4 heading, for mount sites that already print a
   * section label above the thread. 'strip': the h4 sits in the bench header
   * strip, for a mount inside a `.detail-section-container` card (the Scoping
   * Notes & history tab, beside the Implementation notes panel).
   */
  header?: 'plain' | 'strip';
}

export const ModernCommentThread: React.FC<ModernCommentThreadProps> = ({
  commentableType,
  commentableId,
  organizationId,
  readOnly = false,
  header = 'plain',
}) => {
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const confirm = useConfirm();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [members, setMembers] = useState<User[]>([]);
  const [showMentions, setShowMentions] = useState(false);
  const [mentionSearch, setMentionSearch] = useState('');
  const [cursorPosition, setCursorPosition] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    loadComments();
    loadOrganizationMembers();
  }, [commentableType, commentableId]);

  const loadComments = async () => {
    try {
      const data = await apiClient.get(
        `/comments?commentable_type=${commentableType}&commentable_id=${commentableId}`
      );
      setComments(data);
    } catch (error) {
      console.error('Failed to load comments:', error);
    }
  };

  const loadOrganizationMembers = async () => {
    try {
      const data = await apiClient.get(`/organizations/${organizationId}/members`);
      setMembers(data.map((m: any) => m.user).filter(Boolean));
    } catch (error) {
      console.error('Failed to load members:', error);
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    const cursor = e.target.selectionStart;

    setNewComment(value);
    setCursorPosition(cursor);

    // Check if @ was just typed
    const lastAtIndex = value.lastIndexOf('@', cursor - 1);
    if (lastAtIndex !== -1) {
      const textAfterAt = value.substring(lastAtIndex + 1, cursor);
      if (!textAfterAt.includes(' ') && textAfterAt.length < 50) {
        setMentionSearch(textAfterAt.toLowerCase());
        setShowMentions(true);
        return;
      }
    }

    setShowMentions(false);
  };

  const insertMention = (user: User) => {
    if (!textareaRef.current) return;

    const lastAtIndex = newComment.lastIndexOf('@', cursorPosition - 1);
    const before = newComment.substring(0, lastAtIndex);
    const after = newComment.substring(cursorPosition);

    // Use a format that's easier to parse: wrap in special markers
    const mention = `@[${user.display_name || user.email}]`;

    const newValue = before + mention + ' ' + after;
    setNewComment(newValue);
    setShowMentions(false);

    console.log(`Inserted mention for: ${user.display_name || user.email} (ID: ${user.id})`);

    // Focus back on textarea
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  const extractMentionedUserIds = (text: string): string[] => {
    const mentionedIds: string[] = [];

    // Match @[Name] format (inserted by autocomplete)
    const bracketMatches = text.match(/@\[([^\]]+)\]/g) || [];
    bracketMatches.forEach(match => {
      const nameOrEmail = match.substring(2, match.length - 1); // Remove @[ and ]
      const user = members.find(m =>
        (m.display_name && m.display_name.toLowerCase() === nameOrEmail.toLowerCase()) ||
        m.email.toLowerCase() === nameOrEmail.toLowerCase()
      );
      if (user && !mentionedIds.includes(user.id)) {
        mentionedIds.push(user.id);
        console.log(`✅ Extracted mention (bracket format): ${user.display_name || user.email} (ID: ${user.id})`);
      }
    });

    // Also match plain @Name format (for manual typing)
    const plainMatches = text.match(/@([A-Za-z0-9._-]+(?:\s+[A-Za-z0-9._-]+)?)/g) || [];
    plainMatches.forEach(match => {
      // Skip if already matched in bracket format
      if (match.includes('[')) return;

      const nameOrEmail = match.substring(1).trim();
      const user = members.find(m => {
        // Exact match on display name
        if (m.display_name && m.display_name.toLowerCase() === nameOrEmail.toLowerCase()) {
          return true;
        }
        // Exact match on email
        if (m.email.toLowerCase() === nameOrEmail.toLowerCase()) {
          return true;
        }
        // Check if display name is first word(s) of the match
        if (m.display_name) {
          const displayLower = m.display_name.toLowerCase();
          const nameLower = nameOrEmail.toLowerCase();
          if (nameLower.startsWith(displayLower + ' ') || nameLower === displayLower) {
            return true;
          }
        }
        return false;
      });

      if (user && !mentionedIds.includes(user.id)) {
        mentionedIds.push(user.id);
        console.log(`✅ Extracted mention (plain format): ${user.display_name || user.email} (ID: ${user.id})`);
      }
    });

    console.log(`📊 Total mentions extracted: ${mentionedIds.length}`, mentionedIds);
    return mentionedIds;
  };

  const renderContent = (content: string, mentions: string[]) => {
    // First, escape the content to prevent XSS from user input
    // We'll build safe HTML from escaped content
    const escapeHtml = (text: string) => {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    };

    let rendered = escapeHtml(content);

    // First, convert @[Name] format to highlighted spans
    rendered = rendered.replace(/@\[([^\]]+)\]/g, (match, name) => {
      const user = members.find(m =>
        (m.display_name && m.display_name.toLowerCase() === name.toLowerCase()) ||
        m.email.toLowerCase() === name.toLowerCase()
      );

      if (user && mentions.includes(user.id)) {
        return `<span style="${MENTION_STYLE}">@${escapeHtml(name)}</span>`;
      }
      return `@${escapeHtml(name)}`; // Not a valid mention, just display as text
    });

    // Also highlight plain @mentions that are in the mentions array
    members.forEach(user => {
      if (mentions.includes(user.id) && user.display_name) {
        const name = user.display_name;
        // Match @Name but not @[Name]
        const regex = new RegExp(`@(${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})(?!\\])`, 'g');
        rendered = rendered.replace(regex, `<span style="${MENTION_STYLE}">@$1</span>`);
      }
    });

    // Simple markdown: **bold** and *italic*
    rendered = rendered.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    rendered = rendered.replace(/\*(.*?)\*/g, '<em>$1</em>');

    // Code: `code`
    rendered = rendered.replace(/`(.*?)`/g, '<code style="background-color: var(--secondary); padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 0.9em;">$1</code>');

    // Final sanitization with DOMPurify as defense-in-depth
    return DOMPurify.sanitize(rendered, DOMPURIFY_CONFIG);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newComment.trim()) return;

    setLoading(true);
    try {
      const mentionedUserIds = extractMentionedUserIds(newComment);

      await apiClient.post('/comments', {
        commentable_type: commentableType,
        commentable_id: commentableId,
        content: newComment,
        mentions: mentionedUserIds,
        parent_comment_id: replyTo
      });
      setNewComment('');
      setReplyTo(null);
      await loadComments();
    } catch (error) {
      console.error('Failed to create comment:', error);
      alert('Failed to create comment');
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = async (commentId: string) => {
    if (!editContent.trim()) return;

    setLoading(true);
    try {
      const mentionedUserIds = extractMentionedUserIds(editContent);

      await apiClient.patch(`/comments/${commentId}`, {
        content: editContent,
        mentions: mentionedUserIds
      });
      setEditingId(null);
      setEditContent('');
      await loadComments();
    } catch (error) {
      console.error('Failed to edit comment:', error);
      alert('Failed to edit comment');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (commentId: string) => {
    const ok = await confirm({
      title: 'Delete comment',
      message: 'Delete this comment?',
      confirmLabel: 'Delete',
      destructive: true,
    });
    if (!ok) return;

    setLoading(true);
    try {
      await apiClient.delete(`/comments/${commentId}`);
      await loadComments();
    } catch (error) {
      console.error('Failed to delete comment:', error);
      alert('Failed to delete comment');
    } finally {
      setLoading(false);
    }
  };

  const getInitials = (user: User) => {
    if (user.display_name) {
      const parts = user.display_name.split(' ');
      return parts.length > 1
        ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
        : parts[0].substring(0, 2).toUpperCase();
    }
    return user.email.substring(0, 2).toUpperCase();
  };

  // Avatar tones come from the theme's --avatar-* tokens, so both themes get a
  // palette tuned for them. Same hash as before: first char code of the id.
  // An empty id has no first char code (NaN), so it falls back to the first tone.
  const getAvatarTone = (userId: string) => AVATAR_TONES[(userId.charCodeAt(0) || 0) % AVATAR_TONES.length];


  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  };

  // Organize comments into threads
  const topLevelComments = comments.filter(c => !c.parent_comment_id);
  const getReplies = (commentId: string) => comments.filter(c => c.parent_comment_id === commentId);

  const filteredMembers = members.filter(m => {
    if (!mentionSearch) return true;
    const name = (m.display_name || m.email).toLowerCase();
    return name.includes(mentionSearch);
  });


  const renderComment = (comment: Comment, isReply: boolean = false) => {
    const isEditing = !readOnly && editingId === comment.id;
    const replies = getReplies(comment.id);
    const isReplyTarget = replyTo === comment.id;

    return (
      <div key={comment.id} className={`comment-thread-entry${isReply ? ' comment-thread-entry--reply' : ''}`}>
        <div className={`comment-thread-item${isReply ? ' comment-thread-item--reply' : ''}`}>
          <div className={`comment-thread-avatar comment-thread-avatar--${getAvatarTone(comment.user.id)}`}>
            {getInitials(comment.user)}
          </div>

          <div className="comment-thread-main">
            {isEditing ? (
              <div>
                <textarea
                  className="comment-thread-input comment-thread-input--edit"
                  aria-label="Edit comment"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  autoFocus
                />
                <div className="comment-thread-edit-actions">
                  <button
                    type="button"
                    className="btn-primary btn-small"
                    onClick={() => handleEdit(comment.id)}
                    disabled={loading}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-small"
                    onClick={() => {
                      setEditingId(null);
                      setEditContent('');
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="comment-thread-byline">
                  <strong className="comment-thread-author">
                    {comment.user.display_name || comment.user.email}
                  </strong>
                  <span className="comment-thread-meta">
                    {formatDate(comment.created_at)}
                    {comment.is_edited && <span className="comment-thread-edited">(edited)</span>}
                  </span>
                </div>

                <div
                  className="comment-thread-body"
                  dangerouslySetInnerHTML={{ __html: renderContent(comment.content, comment.mentions) }}
                />

                {!readOnly && <div className="comment-thread-actions">
                  <button
                    type="button"
                    className={`btn-text comment-thread-action${isReplyTarget ? ' is-active' : ''}`}
                    aria-pressed={isReplyTarget}
                    onClick={() => setReplyTo(isReplyTarget ? null : comment.id)}
                  >
                    💬 Reply
                  </button>
                  <button
                    type="button"
                    className="btn-text comment-thread-action"
                    onClick={() => {
                      setEditingId(comment.id);
                      setEditContent(comment.content);
                    }}
                  >
                    ✏️ Edit
                  </button>
                  <button
                    type="button"
                    className="btn-text comment-thread-action comment-thread-action--danger"
                    onClick={() => void handleDelete(comment.id)}
                  >
                    🗑️ Delete
                  </button>
                </div>}
              </>
            )}
          </div>
        </div>

        {/* Render replies recursively */}
        {replies.length > 0 && replies.map(reply => renderComment(reply, true))}
      </div>
    );
  };

  return (
    <div className="comment-thread">
      {header === 'strip' ? (
        <div className="container-header bench-header">
          <h4 className="container-title">Comments · {comments.length}</h4>
        </div>
      ) : (
        <h4 className="comment-thread-heading">Comments · {comments.length}</h4>
      )}

      <div className="comment-thread-content">
        <div className="comment-thread-list">
          {comments.length === 0 ? (
            <div className="comment-thread-empty">
              No comments yet. Be the first to comment!
            </div>
          ) : (
            topLevelComments.map(comment => renderComment(comment))
          )}
        </div>

        {/* Reply Indicator */}
        {!readOnly && replyTo && (
          <div className="comment-thread-replying">
            <span>
              💬 Replying to <strong>{comments.find(c => c.id === replyTo)?.user.display_name || 'comment'}</strong>
            </span>
            <button type="button" className="btn-text comment-thread-action is-active" onClick={() => setReplyTo(null)}>
              Cancel
            </button>
          </div>
        )}

        {/* New Comment Form */}
        {!readOnly && <form className="comment-thread-form" onSubmit={handleSubmit}>
          <div className="comment-thread-compose">
            <textarea
              ref={textareaRef}
              className="comment-thread-input"
              aria-label={replyTo ? 'Write a reply' : 'Write a comment'}
              value={newComment}
              onChange={handleInputChange}
              placeholder="Write a comment…"
              disabled={loading}
            />

            {/* Mention Autocomplete Dropdown */}
            {showMentions && filteredMembers.length > 0 && (
              <div className="comment-thread-mentions">
                {filteredMembers.slice(0, 5).map(member => (
                  <div
                    key={member.id}
                    className="comment-thread-mention-option"
                    onClick={() => insertMention(member)}
                  >
                    <div className={`comment-thread-avatar comment-thread-avatar--sm comment-thread-avatar--${getAvatarTone(member.id)}`}>
                      {getInitials(member)}
                    </div>
                    <div>
                      <div className="comment-thread-mention-name">
                        {member.display_name || 'No name'}
                      </div>
                      <div className="comment-thread-mention-email">
                        {member.email}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="comment-thread-footer">
            <div className="comment-thread-tip">
              Tip: Use **bold**, *italic*, `code`, or @mention users
            </div>
            <button
              type="submit"
              className="btn-primary btn-small"
              disabled={loading || !newComment.trim()}
            >
              {loading ? 'Posting…' : replyTo ? 'Post reply' : 'Post comment'}
            </button>
          </div>
        </form>}
      </div>
    </div>
  );
};
