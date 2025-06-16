const mongoose = require('mongoose');

const ResourceSchema = new mongoose.Schema({
  title: String,
  url: String,
  savedBy: [{ type: mongoose.Schema.Types.ObjectId, ref: 'User' }]
}, { timestamps: true });

module.exports = mongoose.model('Resource', ResourceSchema);
