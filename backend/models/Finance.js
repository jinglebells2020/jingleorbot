const mongoose = require('mongoose');

const FinanceSchema = new mongoose.Schema({
  user: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
  revenue: Number,
  expenses: Number,
  notes: String
}, { timestamps: true });

module.exports = mongoose.model('Finance', FinanceSchema);
