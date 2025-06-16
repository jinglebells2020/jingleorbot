const mongoose = require('mongoose');

const ChatMessageSchema = new mongoose.Schema({
  user: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
  message: String,
  fromAI: { type: Boolean, default: false }
}, { timestamps: true });

module.exports = mongoose.model('ChatMessage', ChatMessageSchema);
