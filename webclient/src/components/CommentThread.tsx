import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';

interface Comment {
  id: string;
  content: string;
  user: {
    id: string;
    email: string;
    display_name: string | null;
  };
  created_at: string;
  is_edited: boolean;
  edited_at: string | null;
}

interface CommentThreadProps {
  commentableType: 'control' | 'evidence';
  commentableId: string;
}

export const CommentThread: React.FC<CommentThreadProps> = ({
  commentableType,
  commentableId
}) => {
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState('');
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');

  useEffect(() => {
    loadComments();
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newComment.trim()) return;

    setLoading(true);
    try {
      await apiClient.post('/comments', {
        commentable_type: commentableType,
        commentable_id: commentableId,
        content: newComment,
        mentions: []
      });
      setNewComment('');
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
      await apiClient.patch(`/comments/${commentId}`, {
        content: editContent
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
    if (!confirm('Are you sure you want to delete this comment?')) return;

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

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
  };

  return (
    <div style={{ marginTop: '2rem' }}>
      <h3 style={{ marginBottom: '1rem' }}>Comments</h3>

      {/* Comment List */}
      <div style={{ marginBottom: '1.5rem' }}>
        {comments.length === 0 ? (
          <p style={{ color: '#666', fontSize: '0.875rem' }}>No comments yet</p>
        ) : (
          comments.map(comment => (
            <div
              key={comment.id}
              style={{
                padding: '1rem',
                backgroundColor: '#f5f5f5',
                borderRadius: '4px',
                marginBottom: '1rem'
              }}
            >
              {editingId === comment.id ? (
                <div>
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    style={{
                      width: '100%',
                      minHeight: '80px',
                      padding: '0.5rem',
                      marginBottom: '0.5rem',
                      borderRadius: '4px',
                      border: '1px solid #ddd'
                    }}
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
                        cursor: 'pointer'
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
                        backgroundColor: '#666',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer'
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ marginBottom: '0.5rem' }}>
                    <strong>{comment.user.display_name || comment.user.email}</strong>
                    <span style={{ marginLeft: '0.5rem', fontSize: '0.875rem', color: '#666' }}>
                      {formatDate(comment.created_at)}
                      {comment.is_edited && ' (edited)'}
                    </span>
                  </div>
                  <p style={{ marginBottom: '0.5rem', whiteSpace: 'pre-wrap' }}>
                    {comment.content}
                  </p>
                  <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.875rem' }}>
                    <button
                      onClick={() => {
                        setEditingId(comment.id);
                        setEditContent(comment.content);
                      }}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: '#1976d2',
                        cursor: 'pointer',
                        textDecoration: 'underline'
                      }}
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(comment.id)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: '#d32f2f',
                        cursor: 'pointer',
                        textDecoration: 'underline'
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>

      {/* New Comment Form */}
      <form onSubmit={handleSubmit}>
        <textarea
          value={newComment}
          onChange={(e) => setNewComment(e.target.value)}
          placeholder="Write a comment..."
          disabled={loading}
          style={{
            width: '100%',
            minHeight: '100px',
            padding: '0.75rem',
            marginBottom: '0.5rem',
            borderRadius: '4px',
            border: '1px solid #ddd',
            fontFamily: 'inherit'
          }}
        />
        <button
          type="submit"
          disabled={loading || !newComment.trim()}
          style={{
            padding: '0.5rem 1.5rem',
            backgroundColor: '#1976d2',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: !newComment.trim() ? 'not-allowed' : 'pointer',
            opacity: !newComment.trim() ? 0.5 : 1
          }}
        >
          Post Comment
        </button>
      </form>
    </div>
  );
};
