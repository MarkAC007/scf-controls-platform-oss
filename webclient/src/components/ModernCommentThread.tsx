import React, { useState, useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';
import { apiClient } from '../data/apiClient';

// Configure DOMPurify for safe HTML rendering
// Only allow specific tags and attributes we generate in renderContent
const DOMPURIFY_CONFIG = {
  ALLOWED_TAGS: ['span', 'strong', 'em', 'code'] as string[],
  ALLOWED_ATTR: ['style'] as string[],
  ALLOW_DATA_ATTR: false,
};

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
}

export const ModernCommentThread: React.FC<ModernCommentThreadProps> = ({
  commentableType,
  commentableId,
  organizationId
}) => {
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
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
        return `<span style="background-color: rgba(59, 130, 246, 0.15); color: #3b82f6; padding: 2px 6px; border-radius: 3px; font-weight: 600;">@${escapeHtml(name)}</span>`;
      }
      return `@${escapeHtml(name)}`; // Not a valid mention, just display as text
    });

    // Also highlight plain @mentions that are in the mentions array
    members.forEach(user => {
      if (mentions.includes(user.id) && user.display_name) {
        const name = user.display_name;
        // Match @Name but not @[Name]
        const regex = new RegExp(`@(${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})(?!\\])`, 'g');
        rendered = rendered.replace(regex, `<span style="background-color: rgba(59, 130, 246, 0.15); color: #3b82f6; padding: 2px 6px; border-radius: 3px; font-weight: 600;">@$1</span>`);
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
    if (!confirm('Delete this comment?')) return;

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

  const getAvatarColor = (userId: string) => {
    const colors = ['#1976d2', '#388e3c', '#f57c00', '#7b1fa2', '#0288d1', '#d32f2f'];
    const index = userId.charCodeAt(0) % colors.length;
    return colors[index];
  };

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
    const isEditing = editingId === comment.id;
    const replies = getReplies(comment.id);

    return (
      <div key={comment.id} style={{ marginLeft: isReply ? '3rem' : 0 }}>
        <div
          className="comment-item"
          style={{
            display: 'flex',
            gap: '1rem',
            padding: '1rem',
            backgroundColor: isReply ? 'var(--secondary)' : 'var(--card)',
            borderRadius: '8px',
            marginBottom: '0.75rem',
            border: isReply ? '1px solid var(--border)' : 'none'
          }}
        >
          {/* Avatar */}
          <div
            style={{
              width: '40px',
              height: '40px',
              borderRadius: '50%',
              backgroundColor: getAvatarColor(comment.user.id),
              color: 'white',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 600,
              fontSize: '0.875rem',
              flexShrink: 0
            }}
          >
            {getInitials(comment.user)}
          </div>

          {/* Comment Content */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {isEditing ? (
              /* Edit Mode */
              <div>
                <textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  style={{
                    width: '100%',
                    minHeight: '80px',
                    padding: '0.75rem',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    fontSize: '0.875rem',
                    fontFamily: 'inherit',
                    marginBottom: '0.5rem',
                    backgroundColor: 'var(--panel)',
                    color: 'var(--text)'
                  }}
                  autoFocus
                />
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    onClick={() => handleEdit(comment.id)}
                    disabled={loading}
                    style={{
                      padding: '0.5rem 1rem',
                      backgroundColor: '#1976d2',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '0.875rem',
                      fontWeight: 500
                    }}
                  >
                    Save
                  </button>
                  <button
                    onClick={() => {
                      setEditingId(null);
                      setEditContent('');
                    }}
                    style={{
                      padding: '0.5rem 1rem',
                      backgroundColor: 'var(--secondary)',
                      color: 'var(--muted)',
                      border: '1px solid var(--border)',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '0.875rem'
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              /* View Mode */
              <>
                <div style={{ marginBottom: '0.5rem' }}>
                  <strong style={{ fontSize: '0.9375rem', color: 'var(--text)' }}>
                    {comment.user.display_name || comment.user.email}
                  </strong>
                  <span style={{ marginLeft: '0.75rem', fontSize: '0.8125rem', color: 'var(--muted)' }}>
                    {formatDate(comment.created_at)}
                    {comment.is_edited && <span style={{ marginLeft: '0.5rem', fontStyle: 'italic' }}>(edited)</span>}
                  </span>
                </div>

                <div
                  style={{
                    fontSize: '0.9375rem',
                    lineHeight: '1.6',
                    color: 'var(--text)',
                    marginBottom: '0.75rem',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word'
                  }}
                  dangerouslySetInnerHTML={{ __html: renderContent(comment.content, comment.mentions) }}
                />

                <div style={{ display: 'flex', gap: '1rem', fontSize: '0.8125rem' }}>
                  <button
                    onClick={() => setReplyTo(replyTo === comment.id ? null : comment.id)}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: replyTo === comment.id ? '#3b82f6' : 'var(--muted)',
                      cursor: 'pointer',
                      fontWeight: replyTo === comment.id ? 600 : 400,
                      padding: 0
                    }}
                  >
                    💬 Reply
                  </button>
                  <button
                    onClick={() => {
                      setEditingId(comment.id);
                      setEditContent(comment.content);
                    }}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: 'var(--muted)',
                      cursor: 'pointer',
                      padding: 0
                    }}
                  >
                    ✏️ Edit
                  </button>
                  <button
                    onClick={() => handleDelete(comment.id)}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: '#f87171',
                      cursor: 'pointer',
                      padding: 0
                    }}
                  >
                    🗑️ Delete
                  </button>
                </div>
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
    <div style={{ marginTop: '1.5rem' }}>
      <h4 style={{ marginBottom: '1rem', fontSize: '1rem', fontWeight: 600 }}>
        Comments ({comments.length})
      </h4>

      {/* Comment List */}
      <div style={{ marginBottom: '1.5rem' }}>
        {comments.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--muted)', backgroundColor: 'var(--secondary)', borderRadius: '8px' }}>
            No comments yet. Be the first to comment!
          </div>
        ) : (
          topLevelComments.map(comment => renderComment(comment))
        )}
      </div>

      {/* Reply Indicator */}
      {replyTo && (
        <div
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: 'rgba(59, 130, 246, 0.15)',
            borderRadius: '4px',
            marginBottom: '0.5rem',
            fontSize: '0.875rem',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            color: 'var(--text)'
          }}
        >
          <span>
            💬 Replying to <strong>{comments.find(c => c.id === replyTo)?.user.display_name || 'comment'}</strong>
          </span>
          <button
            onClick={() => setReplyTo(null)}
            style={{
              background: 'none',
              border: 'none',
              color: '#3b82f6',
              cursor: 'pointer',
              fontWeight: 600
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {/* New Comment Form */}
      <form onSubmit={handleSubmit}>
        <div style={{ position: 'relative' }}>
          <textarea
            ref={textareaRef}
            value={newComment}
            onChange={handleInputChange}
            placeholder="Write a comment... Use @ to mention someone, **bold**, *italic*, or `code`"
            disabled={loading}
            style={{
              width: '100%',
              minHeight: '100px',
              padding: '0.75rem',
              border: '2px solid var(--border)',
              borderRadius: '8px',
              fontSize: '0.9375rem',
              fontFamily: 'inherit',
              resize: 'vertical',
              transition: 'border-color 0.2s',
              outline: 'none',
              backgroundColor: 'var(--panel)',
              color: 'var(--text)'
            }}
            onFocus={(e) => e.target.style.borderColor = '#3b82f6'}
            onBlur={(e) => e.target.style.borderColor = 'var(--border)'}
          />

          {/* Mention Autocomplete Dropdown */}
          {showMentions && filteredMembers.length > 0 && (
            <div
              style={{
                position: 'absolute',
                bottom: '100%',
                left: 0,
                marginBottom: '0.5rem',
                backgroundColor: 'var(--card)',
                border: '1px solid var(--border)',
                borderRadius: '8px',
                boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                maxHeight: '200px',
                overflowY: 'auto',
                zIndex: 1000,
                minWidth: '250px'
              }}
            >
              {filteredMembers.slice(0, 5).map(member => (
                <div
                  key={member.id}
                  onClick={() => insertMention(member)}
                  style={{
                    padding: '0.75rem 1rem',
                    cursor: 'pointer',
                    borderBottom: '1px solid var(--border)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.75rem',
                    color: 'var(--text)'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'var(--secondary)'}
                  onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'var(--card)'}
                >
                  <div
                    style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '50%',
                      backgroundColor: getAvatarColor(member.id),
                      color: 'white',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontWeight: 600,
                      fontSize: '0.75rem'
                    }}
                  >
                    {getInitials(member)}
                  </div>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--text)' }}>
                      {member.display_name || 'No name'}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>
                      {member.email}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginTop: '0.75rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>
            Tip: Use **bold**, *italic*, `code`, or @mention users
          </div>
          <button
            type="submit"
            disabled={loading || !newComment.trim()}
            style={{
              padding: '0.625rem 1.5rem',
              backgroundColor: '#1976d2',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              cursor: !newComment.trim() ? 'not-allowed' : 'pointer',
              opacity: !newComment.trim() ? 0.5 : 1,
              fontSize: '0.9375rem',
              fontWeight: 600,
              transition: 'background-color 0.2s'
            }}
            onMouseEnter={(e) => newComment.trim() && (e.currentTarget.style.backgroundColor = '#1565c0')}
            onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#1976d2'}
          >
            {loading ? 'Posting...' : replyTo ? 'Post Reply' : 'Post Comment'}
          </button>
        </div>
      </form>
    </div>
  );
};
